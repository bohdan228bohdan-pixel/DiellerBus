from django.core.management.base import BaseCommand
from main.models import SiteConfig, StaticPage


class Command(BaseCommand):
    help = 'Create default site configuration and common static pages (technical questions, refund policy)'

    def handle(self, *args, **options):
        site = SiteConfig.get_solo()
        # ensure basic contact info exists (admin can later edit)
        if not site.contact_email:
            site.contact_email = 'dieller7073@gmail.com'
        if not site.contact_phone:
            site.contact_phone = '+380957058206'
        if not site.shop_address:
            site.shop_address = 'м. Луцьк'
        if not site.owner_info:
            site.owner_info = 'Власник: ФОП / ТОВ — вкажіть реквізити у налаштуваннях адміністратора.'
        site.save()

        # Technical questions page
        tech_slug = 'technical-questions'
        tech_title = 'Технічні питання — FAQ'
        tech_content = f"""
<p>На цій сторінці зібрані відповіді на типові технічні питання щодо роботи сервісу <strong>{site.shop_name}</strong>.</p>

<h3>1. Як створити акаунт?</h3>
<p>Натисніть «Зареєструватись» у верхньому меню та заповніть форму. Після реєстрації перевірте пошту для підтвердження.</p>

<h3>2. Я не отримав квиток після оплати</h3>
<p>Перевірте папку «Спам». Якщо квитка немає — зверніться в техпідтримку через форму «Технічні питання» або на email <a href=\"mailto:{site.contact_email}\">{site.contact_email}</a>, вкажіть номер замовлення та контактний телефон.</p>

<h3>3. Як змінити контактні дані?</h3>
<p>Зайдіть у профіль (кнопка «Профіль») та відредагуйте телефон або email.</p>

<h3>4. Безпека платежів</h3>
<p>Ми не зберігаємо дані платіжних карт (PAN/CVV) на сервері. Платежі обробляються через платіжні провайдери (Stripe / LiqPay). Для перевірки квитка використовується підпис HMAC, який стає недійсним після повернення коштів.</p>

<h3>5. Технічні питання, які можна описати у зверненні</h3>
<ul>
  <li>Проблеми з оплатою (вкажіть id транзакції або номер замовлення)</li>
  <li>Квиток не прийшов/не відкривається PDF</li>
  <li>Зміна даних пасажира або дати (опціонально — відшкодування)</li>
  <li>Інші технічні збої</li>
</ul>

<p><em>Останній пункт: перегляньте цей розділ та повідомте, якщо потрібно додати або уточнити питання — ми оновимо інформацію.</em></p>
"""
        sp, created = StaticPage.objects.get_or_create(slug=tech_slug, defaults={'title': tech_title, 'content': tech_content, 'language': 'uk', 'is_published': True})
        if not created:
            sp.title = tech_title
            sp.content = tech_content
            sp.is_published = True
            sp.language = 'uk'
            sp.save()

        # Refund policy page
        refund_slug = 'refund-policy'
        refund_title = 'Правила та умови повернення коштів'
        refund_content = f"""
<p>Ці правила описують процедуру повернення коштів за послуги/товари, придбані через сервіс <strong>{site.shop_name}</strong>.</p>

<h3>1. Умови повернення</h3>
<ul>
  <li>Повернення можливо у разі скасування рейсу, помилкової оплати або за домовленістю із техпідтримкою.</li>
  <li>Термін для звернення: протягом 30 календарних днів з дати покупки, якщо інше не погоджено.</li>
</ul>

<h3>2. Процедура</h3>
<ol>
  <li>Зверніться у техпідтримку через форму «Технічні питання» або на email <a href=\"mailto:{site.contact_email}\">{site.contact_email}</a>. Вкажіть номер замовлення, email та телефон.</li>
  <li>Підтвердження вашого запиту може вимагати додаткової інформації (скріншоти, id транзакції, фото документа).</li>
  <li>Після перевірки запиту адміністрація повідомить рішення та орієнтовний термін повернення коштів.</li>
</ol>

<h3>3. Способи повернення</h3>
<p>Повернення здійснюється тим способом, яким була проведена оплата, якщо інше не погоджено. У разі потреби кошти можуть бути переказані на банківський рахунок; реквізити для переказу надайте у зверненні.</p>

<h3>4. Відповідальність та контактні дані</h3>
<p>Власник сервісу: {site.owner_info}</p>
<p>Юридична адреса: {site.shop_address}</p>
<p>Контакти: <a href=\"tel:{site.contact_phone}\">{site.contact_phone}</a>, email: <a href=\"mailto:{site.contact_email}\">{site.contact_email}</a></p>

<p>Якщо у вас є питання щодо повернення — зв'яжіться з нами через форму техпідтримки.</p>
"""
        rp, created2 = StaticPage.objects.get_or_create(slug=refund_slug, defaults={'title': refund_title, 'content': refund_content, 'language': 'uk', 'is_published': True})
        if not created2:
            rp.title = refund_title
            rp.content = refund_content
            rp.is_published = True
            rp.language = 'uk'
            rp.save()

        self.stdout.write(self.style.SUCCESS('SiteConfig and static pages created/updated.'))
