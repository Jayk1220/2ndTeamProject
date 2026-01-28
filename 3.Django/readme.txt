※ api, Django 키는 노션에 올려놨으니 .env 파일로 만들어 3.Django 파일에 넣어야 django 실행이 가능합니다.

1/22
1. 프레임 구현-디자인X
	dashboard 폴더에 views.py와 templates폴더를 중점으로 보면 됨
	ㄴ api로 공항별 날씨가 5초마다 변경되어 나오도록 설정
	ㄴ 공항 현황판처럼 공항별로 항공사, 도착지, 출발시간, 운행여부를 나타내도록 제작하였으나 api문제로 더미로만 제작
	ㄴ 로그인 페이지는 일단 별도로 만들었으나 아직 아이디 생성등의 구현은 X



1/26
2. 공항 현황판 api 
	데이터 키 이름
	- UFID: 항공편 고유 식별자 (날짜+출발지+도착지+편명 조합)
	- BOARDING_KOR / BOARDING_ENG: 출발 공항(한글/영문)
	- ARRIVED_KOR / ARRIVED_ENG: 도착 공항(한글/영문)
	- AIRLINE_KOREAN / AIRLINE_ENGLISH: 항공사 이름(한글/영문)
	- AIR_FLN: 항공편 번호 (예: KE123, LJ251)
	- FLIGHT_DATE: 운항 날짜 (YYYYMMDD 형식)
	- STD: Scheduled Time of Departure → 예정 출발 시각
	- ETD: Estimated Time of Departure → 실제 출발 예상 시각 (지연/변경 시 반영)
	- IO: 출발/도착 구분 (I=International, D=Domestic, O=Outbound 등)
	- LINE_CODE / LINE: 노선 구분 (국내선/국제선)
	- BAGGAGE_CLAIM: 수하물 수취대 번호
	- GATE: 출발 게이트 번호
	- RMK_KOR / RMK_ENG: 비고(지연, 결항 등 안내 메시지)
	- CITY: 도착 도시 코드 (예: KIX = 오사카 간사이)
	- AIRPORT: 출발 공항 코드 (예: GMP = 김포, PUS = 부산 김해)

	1/27일 변경할 것
		컬럼순서 목적지와 편명 순서 변경, 날짜 컬럼 제거
		데이터 출력속도 개선할 부분 찾기

1/27
2. 공항현황판 api
	컬럼 변경, 데이터 5분주기 갱신, 매일 1주일치 데이터 갱신 파일 추가
	장고의 db에 저장완료. 현황판 끝

1/28
3. 챗봇연동
	cmd에서 python manage.py runserver 후
		
	새 cmd창을 열어
	curl -X POST http://127.0.0.1:8000/api/chat/ -H "Content-Type: application/json" -d "{\"message\":\"항공편이 2시간 지연되면 보상을 받을 수 있나요?\"}"
	입력 후  임베딩된 값을 ai에게 넘겨서 확인해 볼 것

	message 값 변경하여 테스트
		대한항공 국내선 2시간 지연이면 보상 있어?

		제주항공 국제선 결항이면 환불/보상 어떻게 돼?

		항공사 모르겠는데 국내선 지연되면 뭐 받을 수 있어?
