import joblib
import pandas as pd
import datetime
import requests # 날씨 API 호출용

class FlightPredictor:
    def __init__(self, clf_path="모델_이진분류.joblib", reg_path="모델_회기분석.joblib"):
        try:
            self.model_clf = joblib.load(clf_path)
            self.model_reg = joblib.load(reg_path)
            print(f"✅ 모델 로드 완료: {clf_path}, {reg_path}")
        except Exception as e:
            print(f"⚠️ 모델 로드 실패: {e}")

    def get_realtime_weather(self, airport_code):
        """
        [2단계] 실제 기상 API(예: OpenWeatherMap 등)와 연동할 구간입니다.
        현재는 모델 학습 데이터의 평균치인 기본값을 반환하도록 설정했습니다.
        """
        # 공항 코드별 좌표 매핑 등을 통해 날씨를 가져오는 로직 추가 가능
        return {"temp": 15.0, "wind": 3.5}

    def predict(self, flight_info, details):
        """
        [3, 4단계] 정보를 받아 예측 수행
        """
        try:
            weather = self.get_realtime_weather(details.get('s_dep'))
            
            # 모델이 학습한 Feature 순서와 동일하게 구성 (매우 중요)
            # feature_names: 기온(°C), 풍속_ms, dep_hour, dep_minute, arrival_code, 
            #               dep_weekday, is_weekend, 항공사, 출발지, flight_type
            
            input_df = pd.DataFrame([{
                "기온(°C)": weather['temp'],
                "풍속_ms": weather['wind'],
                "dep_hour": int(details['dep']['time'][0].split(":")[1].strip()[:2]) if details['dep']['time'] else 10,
                "dep_minute": 0,
                "arrival_code": details.get('s_arr', 'Unknown'),
                "dep_weekday": datetime.datetime.now().weekday(),
                "is_weekend": 1 if datetime.datetime.now().weekday() >= 5 else 0,
                "항공사": flight_info.get("airline_name", "Unknown"),
                "출발지": details.get('s_dep', 'ICN'),
                "flight_type": details.get('route_type', '국제')
            }])

            # 3단계: 지연 여부 예측
            is_delay = self.model_clf.predict(input_df)[0]
            
            # 4단계: 지연 시 회귀 분석 수행
            pred_minutes = 0
            if is_delay == 1:
                pred_minutes = self.model_reg.predict(input_df)[0]
            
            return {
                "is_delay": bool(is_delay),
                "predicted_minutes": round(float(pred_minutes), 1),
                "weather": weather
            }
        except Exception as e:
            print(f"⚠️ 예측 프로세스 오류: {e}")
            return None