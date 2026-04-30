"""
URL configuration for buswebsite project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include

from . import views
from django.contrib.auth.views import LogoutView, PasswordChangeView

urlpatterns = [
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    path('bova/', views.bova, name='bova'),
    path('eos/', views.eos, name='eos'),
    path('kvitokindex/', views.kvitokindex, name='kvitokindex'),
    path('cities/', views.cities_table, name='cities_table'),
    path('api/trips/', views.api_trips, name='api_trips'),
    path('api/cities/', views.api_cities, name='api_cities'),
    path('mercedes2/', views.mercedes2, name='mercedes2'),
    path('nashbusindex/', views.nashbusindex, name='nashbusindex'),
    path('neolplanwhite/', views.neolplanwhite, name='neolplanwhite'),
    path('neoplanred/', views.neoplanred, name='neoplanred'),
    path('oplata/', views.oplata, name='oplata'),
    path('registerindex/', views.registerindex, name='registerindex'),
    path('profile/', views.profile, name='profile'),
    path('logout/', LogoutView.as_view(next_page='home'), name='logout'),
    path('nashbusindex/eos.html', views.eos, name='nashbus_eos_html'),
    path('nashbusindex/bova.html', views.bova, name='nashbus_bova_html'),
    path('nashbusindex/mercedes2.html', views.mercedes2, name='nashbus_mercedes2_html'),
    path('nashbusindex/Neoplanred.html', views.neoplanred, name='nashbus_neoplanred_html'),
    path('nashbusindex/neolplanwhite.html', views.neolplanwhite, name='nashbus_neolplanwhite.html'),
    path('eos/', views.eos, name='eos'),
    path("register/", views.registerindex, name="registerindex"),
    path("verify-email/", views.verify_email, name="verify_email"),
    
    path("create-ticket/", views.create_ticket, name="create_ticket"),
    path("payment-success/", views.payment_success),
    path("payment-cancel/", views.payment_cancel),
    path("oplata/", views.oplata, name="oplata"),

    path('password_change/', PasswordChangeView.as_view(template_name='registration/password_change_form.html', success_url='/profile/'), name='password_change'),
]


