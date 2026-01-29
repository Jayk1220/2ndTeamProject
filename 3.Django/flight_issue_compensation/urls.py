"""
URL configuration for flight_issue_compensation project.

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
from dashboard.views import dashboard_view, api_airport_weather_simple
from dashboard.views import api_departures, api_arrivals
from dashboard.views import api_weather

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", dashboard_view),
    path("api/airport-weather/", api_airport_weather_simple),
    path("accounts/", include("django.contrib.auth.urls")), 
    path("api/departures/", api_departures),
    path("api/arrivals/", api_arrivals),
    path("", include("chatbot.urls")),
    path("api/weather/", api_weather),
]
