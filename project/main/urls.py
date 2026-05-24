# project/main/urls.py
from django.urls import path, reverse_lazy
from django.contrib.auth.views import LogoutView, PasswordChangeView
from . import views
from .views import BusDetailView
from .views import bus_detail

app_name = "main"

urlpatterns = [
    # root
    path("", views.home, name="home"),

    # content pages
    path("about/", views.about, name="about"),
    path("bova/", views.bova, name="bova"),
    path("eos/", views.eos, name="eos"),
    path("kvitokindex/", views.kvitokindex, name="kvitokindex"),
    path("cities/", views.cities_table, name="cities_table"),
    path("mercedes2/", views.mercedes2, name="mercedes2"),
    path("nashbusindex/", views.nashbusindex, name="nashbusindex"),
    path("neolplanwhite/", views.neolplanwhite, name="neolplanwhite"),
    path("neoplanred/", views.neoplanred, name="neoplanred"),
    path("oplata/", views.oplata, name="oplata"),
    path("agreements/", views.agreements, name="agreements"),

    # api
    path("api/trips/", views.api_trips, name="api_trips"),
    path("api/cities/", views.api_cities, name="api_cities"),
    path("api/check-user/", views.check_user_availability, name="check_user_availability"),

    # auth/registration
    path("registerindex/", views.registerindex, name="registerindex"),
    path("register/", views.registerindex, name="register"),
    path("verify-email/", views.verify_email, name="verify_email"),
    path("resend-verification/", views.resend_verification_code, name="resend_verification"),
    path("profile/", views.profile, name="profile"),
    path("logout/", views.logout_view, name="logout"),
    path(
        "password_change/",
        views.request_password_change,
        name="password_change",
    ),

    # payments/tickets
    path("create-ticket/", views.create_ticket, name="create_ticket"),
    # Fallback route: support direct /checkout/?trip=<id>&date=... (avoid 404 when id missing)
    path("checkout/", views.checkout_root, name="checkout_root"),
    path("buy/<int:trip_id>/", views.buy_trip, name="buy_trip"),
    path("checkout/<int:trip_id>/", views.checkout, name="checkout"),
    path("debug/liqpay/", views.liqpay_debug, name="liqpay_debug"),
    path("liqpay-callback/", views.liqpay_callback, name="liqpay_callback"),
    path("ticket/<int:ticket_id>/download/", views.download_ticket, name="download_ticket"),
    path("ticket/verify/<int:ticket_id>/<str:signature>/", views.ticket_verify, name="ticket_verify"),
    path("cancellation/<int:ticket_id>/", views.cancellation_manage, name="cancellation_manage"),
    path("payment-success/", views.payment_success, name="payment_success"),
    path("payment-cancel/", views.payment_cancel, name="payment_cancel"),
    path("buses/", views.BusListView.as_view(), name="bus_list"),
    path("buses/<slug:slug>/book/", views.bus_booking, name="bus_booking"),
    path('bus/<slug:slug>/', views.bus_detail, name='bus_detail'),
    path("contacts/", views.contacts, name="contacts"),
    # static DB-managed pages
    path("page/<slug:slug>/", views.static_page, name="static_page"),
    # language switcher
    path("set-language/", views.set_language, name="set_language"),
    # support
    path("support/", views.support_home, name="support_home"),
    path("support/<int:ticket_id>/", views.support_ticket_detail, name="support_ticket_detail"),
    path("support/admin/", views.support_admin_list, name="support_admin"),
    path("support/admin/cancel-trip/", views.support_admin_cancel_trip, name="support_admin_cancel_trip"),
    path("support/admin/take/<int:ticket_id>/", views.support_admin_take, name="support_admin_take"),
    path("support/admin/close/<int:ticket_id>/", views.support_admin_close, name="support_admin_close"),
    path("support/admin/user/<int:user_id>/", views.support_admin_user, name="support_admin_user"),
    path("support/admin/ticket/<int:ticket_id>/send-rebook/", views.support_admin_send_rebook, name="support_admin_send_rebook"),
        path("support/admin/ticket/<int:ticket_id>/resend/", views.support_resend_ticket, name="support_resend_ticket"),
        path("support/admin/user/<int:user_id>/edit/", views.support_edit_user, name="support_edit_user"),
    path("support/admin/ticket/<int:ticket_id>/attach/", views.support_admin_attach, name="support_admin_attach"),
    # support worker frontend
    path("support/worker/", views.support_worker_queue, name="support_worker_queue"),
    path("support/worker/take/<int:ticket_id>/", views.support_worker_take, name="support_worker_take"),
    path("support/api/my-ticket/", views.support_api_my_ticket, name="support_api_my_ticket"),
    path("support/api/send/<int:ticket_id>/", views.support_api_send_message, name="support_api_send_message"),
    path("support/api/close-popup/", views.support_close_popup, name="support_close_popup"),
    # ticket view/edit/refund
    path("ticket/<int:ticket_id>/view/", views.ticket_view, name="ticket_view"),
    path("ticket/<int:ticket_id>/edit/", views.ticket_edit, name="ticket_edit"),
    path("ticket/<int:ticket_id>/refund/", views.ticket_refund, name="ticket_refund"),
    path("password-change/verify/", views.verify_password_change, name="verify_password_change"),
    path("password-change/resend/", views.resend_password_change_code, name="resend_password_change_code"),
    # carrier dashboard
    path("carrier/dashboard/", views.carrier_dashboard, name="carrier_dashboard"),
    path("carrier/tickets/<int:year>/<int:month>/", views.carrier_tickets_month, name="carrier_tickets_month"),
    path("carrier/tickets/<int:year>/<int:month>/export/csv/", views.carrier_tickets_month_export_csv, name="carrier_tickets_month_export_csv"),
    path("carrier/tickets/<int:year>/<int:month>/export/pdfs/", views.carrier_tickets_month_export_pdfs, name="carrier_tickets_month_export_pdfs"),
    # carrier trip management
    path("carrier/trips/", views.carrier_manage_trips, name="carrier_manage_trips"),
    path("carrier/trip/<int:trip_id>/toggle/", views.carrier_toggle_trip, name="carrier_toggle_trip"),
    path("carrier/trip/<int:trip_id>/availability/", views.carrier_trip_availability, name="carrier_trip_availability"),
]