from django.db import models
from django.contrib.auth.models import User

# Create your models here.
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    phone = models.BigIntegerField()

    def __str__(self):
        return self.user.get_full_name() or self.user.username
    

class Prediction(models.Model):

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='predictions')

    # Soil nutrients
    N = models.FloatField()
    P = models.FloatField()
    K = models.FloatField()

    # Environment features
    Temperature = models.FloatField()   # kept capitalized (matches your existing DB field)
    Humidity = models.FloatField()      # kept capitalized
    PH = models.FloatField()            # kept capitalized
    Rainfall = models.FloatField()      # kept capitalized

    predicted_label = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} -> {self.predicted_label}"   # removed space bug after self.