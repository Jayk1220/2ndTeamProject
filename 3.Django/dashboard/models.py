# dashboard/models.py
from django.db import models


class FlightSnapshot(models.Model):
    KIND_CHOICES = (
        ("dep", "Departure"),
        ("arr", "Arrival"),
    )

    airport_code = models.CharField(max_length=5)     # 선택 공항 (CJU, CJJ 등)
    kind = models.CharField(max_length=3, choices=KIND_CHOICES)

    flight_date = models.CharField(max_length=8)      # YYYYMMDD
    std = models.CharField(max_length=4)              # hhmm

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
            "airport_code",
            "kind",
            "flight_date",
            "std",
            "flight_no",
            "origin",
            "destination",
        )

    def __str__(self):
        return f"{self.flight_date} {self.std} {self.flight_no} ({self.airport_code}/{self.kind})"
