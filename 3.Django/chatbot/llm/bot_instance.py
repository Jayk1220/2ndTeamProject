import subprocess
import os
import sys
import re
import threading
import time

# ANSI 제어 문자 제거
ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

class GlobalFlightBot:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.process = None
        self.start_process()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def start_process(self):
        """chatbot.py가 bot_instance.py와 같은 폴더에 있을 때의 경로 설정"""
        # 1. 현재 파일(bot_instance.py)이 있는 폴더 경로 가져오기
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # 2. 같은 폴더 내의 chatbot.py 경로 생성
        script_path = os.path.join(current_dir, "chatbot.py")

        # [디버깅] 실제 경로가 맞는지 터미널에 출력해서 확인
        print(f"🔍 [Path Check] 엔진 경로: {script_path}")

        if not os.path.exists(script_path):
            print(f"❌ [Error] 파일을 찾을 수 없습니다! 실제 위치를 확인하세요.")
            # 혹시 모르니 상위 폴더도 한번 더 체크 (방어적 코드)
            script_path = os.path.abspath(os.path.join(current_dir, "..", "chatbot.py"))

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        print(f"🚀 [GlobalBot] 엔진 시동 시도...")
        
        self.process = subprocess.Popen(
            [sys.executable, "-u", script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, 
            text=True,
            encoding='utf-8',
            env=env
        )

    def send_message_stream(self, message):
        """'n' 입력 시 엔진을 재시작하여 컨텍스트를 초기화합니다."""
        
        # 1. 사용자가 'n' 또는 'N'을 입력한 경우 처리
        if message.strip().lower() == 'n':
            yield ""
            if self.process:
                self.process.terminate() # 기존 프로세스 종료
                self.process.wait()      # 완전히 죽을 때까지 대기
            self.start_process()         # 새 프로세스 시동
            yield "새로운 질문을 입력해주세요!"
            return

        # 2. 일반 질문일 경우 (기존 로직)
        if self.process is None or self.process.poll() is not None:
            self.start_process()

        try:
            self.process.stdin.write(message + "\n")
            self.process.stdin.flush()
        except Exception:
            self.start_process()
            return

        line_buffer = ""
        while True:
            char = self.process.stdout.read(1)
            if not char:
                if self.process.poll() is not None: break
                time.sleep(0.01)
                continue
            
            yield char
            line_buffer += char

            if char == '\n':
                line_buffer = ""
                continue

            clean_buffer = ansi_escape.sub('', line_buffer).strip()
            # 챗봇이 다음 대화를 위해 프롬프트를 띄우면 이번 턴 종료
            if any(p in clean_buffer for p in ["사용자:", "건너뛰기):", "취소):"]):
                if clean_buffer.endswith(":") or clean_buffer.endswith(" "):
                    break