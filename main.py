# main.py
from douban_reader import DoubanReader

EBOOK_ID   = "450696"
BOOK_TITLE = None
ROOT_DIR   = None
START_PAGE = 1
END_PAGE   = 200

COOKIE_FILE = "./cookie.txt"

OPPORTUNISTIC   = True
MAX_DIRECT      = 15
USE_SEARCH_JUMP = True

# 无头模式开关
HEADLESS = False             # ← True = 无头，False = 有头


def main():
    with DoubanReader(
        ebook_id=EBOOK_ID,
        book_title=BOOK_TITLE,
        cookie_file=COOKIE_FILE,
        headless=HEADLESS,      # ← 传进来
    ) as r:
        r.open()
        print(f"书名: {r.get_book_title()}")
        print(f"总页数: {r.total_pages()}")

        result = r.scrape_to_files(
            start=START_PAGE,
            end=END_PAGE,
            root_dir=ROOT_DIR,
            opportunistic=OPPORTUNISTIC,
            max_direct=MAX_DIRECT,
            use_search_jump=USE_SEARCH_JUMP,
        )

        print(f"\n本次正式抓取   {result['pages_fetched']} 页")
        print(f"缓存总页数     {result['cached_pages']}")
        print(f"其中正式页     {result['intentional_pages']}")
        print(f"仅缓存(顺手)   {result['cache_only_pages']}")
        print(f"输出章节       {len(result['txt'])} 个")
        if result.get("error"):
            print(f"⚠️  抓取过程出错: {result['error']}")
        for p in result["txt"]:
            print("  →", p)


if __name__ == "__main__":
    main()