#!/usr/bin/env python3
import http.server
import socketserver
import json
import os
from pathlib import Path
import datetime
import sys

ROOT = Path(__file__).resolve().parents[1]
MAPS_DIR = ROOT / 'data' / 'maps'
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000

class TriggerHandler(http.server.SimpleHTTPRequestHandler):
    # 让静态文件根目录是仓库 ROOT
    def translate_path(self, path):
        # 复用 SimpleHTTPRequestHandler 的逻辑，但基准目录改成 ROOT
        base = ROOT
        # copy from parent but simplified:
        path = path.split('?',1)[0].split('#',1)[0]
        path = os.path.normpath(path.lstrip('/'))
        return str(base / path)
    
    def end_headers(self):
        # 禁用所有静态资源缓存（HTML + JSON + JS + CSS）
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        super().end_headers()

    def do_POST(self):
        if self.path != '/__save_layout':
            self.send_error(404, 'Unknown endpoint')
            return

        length = int(self.headers.get('Content-Length', '0') or '0')
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode('utf-8'))
        except Exception as e:
            self.send_error(400, f'Bad JSON: {e}')
            return

        map_name = payload.get('map_name')
        node_positions = payload.get('node_positions')

        if not map_name or not isinstance(node_positions, dict):
            self.send_error(400, 'Missing map_name or node_positions')
            return

        # 组装要写入的布局文件
        tool_version = payload.get('tool_version', 'unknown')
        generated_at = payload.get('generated_at') or datetime.datetime.now().isoformat()

        layout = {
            "tool_version": tool_version,
            "generated_at": generated_at,
            "node_positions": node_positions
        }

        # 落盘路径：data/maps/<map_name>/<map_name>_layout.json
        out_dir = MAPS_DIR / map_name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{map_name}_layout.json"

        try:
            with out_path.open('w', encoding='utf-8') as f:
                json.dump(layout, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.send_error(500, f'Write failed: {e}')
            return

        # 返回一个很简单的 JSON 响应
        resp = {
            "ok": True,
            "path": str(out_path.relative_to(ROOT))
        }
        data = json.dumps(resp).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    os.chdir(ROOT)
    with socketserver.TCPServer(("", PORT), TriggerHandler) as httpd:
        print(f"Serving trigger graphs at http://localhost:{PORT}/ (root={ROOT})")
        httpd.serve_forever()

if __name__ == '__main__':
    main()
