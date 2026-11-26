"""兼容入口：旧脚本名保留，实际走 tools.evalkit.run。"""
from tools.evalkit.run import main

if __name__ == "__main__":
    raise SystemExit(main())
