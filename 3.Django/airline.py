
import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("airline_key")

AIRLINE_URL = "https://api.odcloud.kr/api/FlightStatusListDTL/v1/getFlightStatusListDetail"

params = {
    "page": 158398,       # 예시 페이지
    "perPage": 10,  # 한번에 가져올 데이터 수
    "serviceKey": key
}

r = requests.get(AIRLINE_URL, params=params)
data = r.json().get("data", [])

print(data)

results = []

# 현재 시각
# now = datetime.now()

# for flight in data:
#     flight_date_str = flight.get("FLIGHT_DATE")
#     std_str = flight.get("STD")  # "hhmm" 형식

#     if not flight_date_str or not std_str:
#         continue

#     try:
#         # FLIGHT_DATE + STD를 datetime으로 변환
#         flight_datetime = datetime.strptime(flight_date_str + std_str, "%Y%m%d%H%M")

#         # 오늘 이후 + 현재 시각 이후만 필터링
#         if flight_datetime >= now:
#             record = {
#                 "출발지": flight.get("BOARDING_KOR"),
#                 "도착지": flight.get("ARRIVED_KOR"),
#                 "항공사": flight.get("AIRLINE_KOREAN"),
#                 "편명": flight.get("AIR_FLN"),
#                 "운항일자": flight.get("FLIGHT_DATE"),
#                 "출발예정": flight.get("STD"),
#                 "비고": flight.get("RMK_KOR"),
#                 "도시코드": flight.get("CITY"),
#                 "공항코드": flight.get("AIRPORT")
#             }
#             results.append(record)
#     except ValueError:
#         # 변환 실패 시 무시
#         continue

# # 결과 출력
# for f in results[:10]:  # 앞 10개만 예시 출력
#     print(f)