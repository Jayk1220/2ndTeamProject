# dashboard/models.py

from django.db import models


class FlightSnapshot(models.Model):
    KIND_CHOICES = (
        ("dep", "Departure"),
        ("arr", "Arrival"),
    )

    airport_code = models.CharField(max_length=5)
    kind = models.CharField(max_length=3, choices=KIND_CHOICES)

    flight_date = models.CharField(max_length=8)  # YYYYMMDD
    std = models.CharField(max_length=4)          # hhmm

    airline = models.CharField(max_length=50)
    origin = models.CharField(max_length=50)
    destination = models.CharField(max_length=50)
    flight_no = models.CharField(max_length=10)

    status = models.CharField(max_length=20, default="정상")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["airport_code", "kind", "flight_date", "std"]),
        ]
        unique_together = (
            "airport_code", "kind", "flight_date", "std",
            "flight_no", "origin", "destination",
        )


class WeatherSnapshot(models.Model):
    airport_code = models.CharField(max_length=3, db_index=True)  # ICN, GMP...
    stn = models.CharField(max_length=5)                          # 113, 110...
    observed_at = models.DateTimeField(db_index=True)             # 관측시각

    ta = models.FloatField(null=True, blank=True)                 # 기온
    ws02 = models.FloatField(null=True, blank=True)               # 2분 평균 풍속
    ws02_max = models.FloatField(null=True, blank=True)
    l_vis = models.IntegerField(null=True, blank=True)
    r_vis = models.IntegerField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["airport_code", "observed_at"],
                name="uniq_weather_airport_observed_at",
            )
        ]

    def __str__(self):
        return f"[{self.airport_code}] {self.observed_at:%Y-%m-%d %H:%M} TA={self.ta} WS02={self.ws02}"
    
class WeatherCurrent(models.Model):
    airport_code = models.CharField(max_length=3, unique=True)  # ✅ 공항별 1행
    stn = models.CharField(max_length=5)
    observed_at = models.DateTimeField()
    ta = models.FloatField(null=True, blank=True)
    ws02 = models.FloatField(null=True, blank=True)
    ws02_max = models.FloatField(null=True, blank=True)
    l_vis = models.IntegerField(null=True, blank=True)
    r_vis = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
