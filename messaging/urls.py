from django.urls import path
from messaging import views

urlpatterns = [
    path('conversations/', views.ConversationListView.as_view(), name='conversation-list'),
    path('conversations/<uuid:conversation_id>/delete/', views.ConversationDeleteView.as_view(), name='conversation-delete'),
    path('chat-token/', views.ChatTokenView.as_view(), name='chat-token'),
    path('hub-token/', views.HubTokenView.as_view(), name='hub-token'),
    path('webhook/edge-chat/', views.EdgeChatWebhookView.as_view(), name='edge-chat-webhook'),
    path('uploads/', views.ChatUploadURLView.as_view(), name='chat-uploads'),
    path('uploads/direct/', views.ChatUploadDirectView.as_view(), name='chat-uploads-direct'),
]
