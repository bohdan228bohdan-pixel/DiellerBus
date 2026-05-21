import os
import sys

# Ensure Django settings are loaded
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'buswebsite.settings')
# ensure project root is on sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# add the 'project' folder (where buswebsite package lives) to PYTHONPATH
sys.path.insert(0, os.path.join(ROOT, 'project'))
import django
django.setup()

from main.models import Ticket
from main.views import _generate_ticket_pdf_bytes

def main():
    t = Ticket.objects.order_by('-id').first()
    if not t:
        print('No tickets found in DB')
        return
    print('Using ticket id=', t.id)
    pdf = _generate_ticket_pdf_bytes(t)
    if not pdf:
        print('PDF generation returned None')
        return
    out = 'ticket_test.pdf'
    with open(out, 'wb') as f:
        f.write(pdf)
    print('Wrote', out, 'len=', len(pdf))

if __name__ == '__main__':
    main()
