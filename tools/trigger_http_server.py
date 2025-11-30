#!/usr/bin/env python3
import http.server
import socketserver
import json
import os
from pathlib import Path
import datetime
import sys
import urllib.parse   # 用于解析 ?skip_physics=1 之类的查询参数
import re

ROOT = Path(__file__).resolve().parents[1]
MAPS_DIR = ROOT / 'data' / 'maps'
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000


class TriggerHandler(http.server.SimpleHTTPRequestHandler):
    # 让静态文件根目录是仓库 ROOT
    def translate_path(self, path):
        # 复用 SimpleHTTPRequestHandler 的逻辑，但基准目录改成 ROOT
        base = ROOT
        # copy from parent but simplified:
        path = path.split('?', 1)[0].split('#', 1)[0]
        path = os.path.normpath(path.lstrip('/'))
        return str(base / path)

    def end_headers(self):
        # 禁用所有静态资源缓存（HTML + JSON + JS + CSS）
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        super().end_headers()
        

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path_only = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        skip_physics = (query.get('skip_physics', ['0'])[0] == '1')

        def _disable_physics_in_html(html: str) -> str:
            """
            尝试在 pyvis 生成的 HTML 中，把 options 里的 physics.enabled 改成 false。
            匹配失败就原样返回，尽量“温和”处理。
            """
            # 典型片段类似：
            # "physics": {"enabled": true, "stabilization": {...}, ...}
            pattern = r'"physics"\s*:\s*{[^}]*?"enabled"\s*:\s*true'

            def repl(m: re.Match) -> str:
                block = m.group(0)
                # 只改这一块里的第一个 enabled:true，避免误伤其它 true
                block2 = re.sub(r'"enabled"\s*:\s*true',
                                '"enabled": false',
                                block,
                                count=1)
                return block2

            new_html, n = re.subn(pattern, repl, html, count=1)
            return new_html if n > 0 else html

        # 只对 HTML + skip_physics=1 做特殊处理
        if skip_physics and path_only.endswith('.html'):
            fs_path = self.translate_path(path_only)
            if not os.path.exists(fs_path):
                return super().do_GET()

            try:
                with open(fs_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception:
                return super().do_GET()

            # ⭐ 第一步：把 pyvis 初始化里的 physics.enabled 改成 false
            content = _disable_physics_in_html(content)

            # ⭐ 第二步：注入我们之前的“隐藏 canvas + 等缓存 + fit()”脚本
            injection = r"""
<script type="text/javascript">
(function(){
  if (window.location.search.indexOf('skip_physics=1') === -1) return;

  function getCanvas(){
    try{
      var container = document.getElementById('mynetwork');
      if (container){
        return container.querySelector('canvas.vis-network') ||
               container.querySelector('canvas');
      }
      return document.querySelector('canvas.vis-network') ||
             document.querySelector('canvas');
    }catch(e){
      return null;
    }
  }

  // 主动把 pyvis 的 loadingBar 干掉
  function hidePyVisLoadingBar(){
    try{
      var lb  = document.getElementById('loadingBar');
      var lbt = document.getElementById('loadingBar_text');
      if (lb)  lb.style.display  = 'none';
      if (lbt) lbt.style.display = 'none';
    }catch(e){}
  }

  function hideCanvas(){
    try{
      var cv = getCanvas();
      if (!cv) return;
      cv.style.opacity = '0';
    }catch(e){}
  }

  function showWhenLayoutReady(){
    var start = (window.performance && performance.now) ? performance.now() : Date.now();
    var timeoutMs = 4000;

    var timer = setInterval(function(){
      var now = (window.performance && performance.now) ? performance.now() : Date.now();
      var ready   = (window.__LAYOUT_APPLIED === true);
      var elapsed = now - start;

      if (!ready && elapsed <= timeoutMs){
        return;
      }
      clearInterval(timer);

      // 再保险隐藏一次 loadingBar
      hidePyVisLoadingBar();

      try{
        var cv = getCanvas();
        if (cv){
          cv.style.transition = 'opacity 160ms ease';
          cv.style.opacity = '1';
        }
      }catch(e){}

      try{
        if (ready && typeof network !== 'undefined' && network && typeof network.fit === 'function'){
          network.fit({ animation: false });
        }
      }catch(e){}
    }, 60);
  }

  // 这里的 disablePhysics 成为“兜底”，但正常情况下 options 中已经是 enabled:false
  function disablePhysics() {
    try {
      if (typeof network === 'undefined' || !network) {
        return setTimeout(disablePhysics, 60);
      }
      network.setOptions({
        physics: {
          enabled: false,
          stabilization: { enabled: false }
        }
      });
      if (typeof network.stopSimulation === 'function') {
        network.stopSimulation();
      }
    } catch (e) {}
  }

  function initSkipPhysics(){
    hidePyVisLoadingBar();
    hideCanvas();
    showWhenLayoutReady();
    disablePhysics();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSkipPhysics, { once: true });
  } else {
    initSkipPhysics();
  }
})();
</script>
"""

            lower = content.lower()
            idx = lower.rfind('</body>')
            if idx != -1:
                patched = content[:idx] + injection + content[idx:]
            else:
                patched = content + injection

            data = patched.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # 其它情况直接走默认逻辑
        return super().do_GET()

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
