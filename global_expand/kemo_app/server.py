"""兼容入口：旧调用继续执行 FastAPI daemon。"""

from daemon import main


if __name__ == "__main__":
    main()
