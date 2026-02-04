from django.urls import path
from .views import api_chat, chat_stream_view # 스트리밍 뷰 추가
from . import views

urlpatterns = [
    path("api/chat/", api_chat, name="api_chat"),
    # 스트리밍 요청을 받을 새로운 경로
    path('api/weather/', views.get_weather, name='get_weather'),
    path('api/departures/', views.get_departures, name='get_departures'),
    path("chat/stream/", chat_stream_view, name="chat_stream"),
]