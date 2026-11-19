# Fix the paginate helper

The working directory contains `paginate.py`. `paginate(items, page, per_page)` should return the items for one page. Pages are numbered from 1. A page past the end returns an empty list, and a page below 1 returns an empty list.

Right now the last item of every full page is dropped. Fix the function. Do not change its signature.

## Done when

`paginate(list(range(10)), 1, 4)` returns `[0, 1, 2, 3]`, `paginate(list(range(10)), 3, 4)` returns `[8, 9]`, and `paginate(list(range(10)), 4, 4)` returns `[]`.
