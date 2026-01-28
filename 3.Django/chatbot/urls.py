from django.urls import path
from .views import api_chat

urlpatterns = [
    path("api/chat/", api_chat, name="api_chat"),
]
