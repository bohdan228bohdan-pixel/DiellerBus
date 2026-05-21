from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from .models import SupportTicket, SupportMessage

class SupportConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.ticket_id = self.scope['url_route']['kwargs'].get('ticket_id')
        self.user = self.scope.get('user')
        if not self.user or self.user.is_anonymous:
            await self.close()
            return
        ticket = await self.get_ticket(self.ticket_id)
        if not ticket:
            await self.close()
            return
        # allow only ticket owner or staff
        if not (self.user == ticket.user or self.user.is_staff or self.user.is_superuser):
            await self.close()
            return

        self.group_name = f'support_ticket_{self.ticket_id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # send recent messages on connect
        msgs = await self.get_recent_messages(self.ticket_id)
        await self.send_json({
            'type': 'init',
            'messages': msgs,
            'ticket': {
                'id': int(self.ticket_id),
                'status': ticket.status,
                'assigned_to': ticket.assigned_to.username if ticket.assigned_to else None,
                'subject': ticket.subject or (ticket.preset.title if ticket.preset else ''),
                'created_at': timezone.localtime(ticket.created_at).strftime('%d.%m.%Y %H:%M') if ticket.created_at else ''
            }
        })

    async def disconnect(self, close_code):
        try:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception:
            pass

    async def receive_json(self, content):
        action = content.get('action')
        if action == 'send_message':
            text = (content.get('text') or '').strip()
            if not text:
                return
            msg = await self.create_message(text)
            message_data = await self.serialize_message(msg)
            data = {'type': 'new_message', 'message': message_data}
            await self.channel_layer.group_send(self.group_name, {'type': 'broadcast.message', 'data': data})

    async def broadcast_message(self, event):
        await self.send_json(event['data'])

    @database_sync_to_async
    def get_ticket(self, ticket_id):
        try:
            return SupportTicket.objects.get(pk=ticket_id)
        except Exception:
            return None

    @database_sync_to_async
    def get_recent_messages(self, ticket_id, limit=30):
        qs = SupportMessage.objects.filter(ticket_id=ticket_id).select_related('sender', 'sender__profile').order_by('-created_at')[:limit]
        out = []
        for m in reversed(list(qs)):
            try:
                created = timezone.localtime(m.created_at).strftime('%d.%m.%Y %H:%M')
            except Exception:
                created = str(m.created_at)
            sender_avatar = None
            try:
                if m.sender and getattr(m.sender, 'profile', None) and m.sender.profile.avatar:
                    sender_avatar = m.sender.profile.avatar.url
            except Exception:
                sender_avatar = None
            out.append({
                'id': m.id,
                'text': m.text,
                'sender': m.sender.username if m.sender else 'Система',
                'is_from_admin': bool(m.is_from_admin),
                'created_at': created,
                'attachment': (m.attachment.url if m.attachment else None),
                'sender_avatar': sender_avatar,
            })
        return out

    @database_sync_to_async
    def create_message(self, text):
        ticket = SupportTicket.objects.get(pk=self.ticket_id)
        msg = SupportMessage.objects.create(ticket=ticket, sender=self.user, text=text, is_from_admin=bool(self.user.is_staff))
        return msg

    @database_sync_to_async
    def serialize_message(self, msg):
        try:
            created = timezone.localtime(msg.created_at).strftime('%d.%m.%Y %H:%M')
        except Exception:
            created = str(msg.created_at)
        sender_avatar = None
        try:
            if msg.sender and getattr(msg.sender, 'profile', None) and msg.sender.profile.avatar:
                sender_avatar = msg.sender.profile.avatar.url
        except Exception:
            sender_avatar = None
        return {
            'id': msg.id,
            'text': msg.text,
            'sender': msg.sender.username if msg.sender else 'Система',
            'is_from_admin': bool(msg.is_from_admin),
            'created_at': created,
            'attachment': (msg.attachment.url if msg.attachment else None),
            'sender_avatar': sender_avatar,
        }
