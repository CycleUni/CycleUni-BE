from rest_framework import serializers
from ads.models import Ad

class PublicAdSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ad
        fields = ('id', 'title', 'image_url', 'target_url', 'position')
