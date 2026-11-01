"""In ra cổng trống đầu tiên trong 8001–8020 (dùng bởi start_windows.bat)."""
import socket
for port in range(8001, 8021):
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1', port))
        except OSError:
            continue
    print(port)
    break
else:
    raise SystemExit(1)
