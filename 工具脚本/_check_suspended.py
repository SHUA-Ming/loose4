import pymysql
conn = pymysql.connect(host='localhost',port=3306,user='root',password='123456',database='stock_local')
c = conn.cursor()

# 今日停牌
c.execute("SELECT COUNT(*) FROM kline_daily WHERE date = '2026-05-08' AND (volume IS NULL OR volume = 0)")
print('今日volume=0或NULL:', c.fetchone()[0])

# 持续停牌: 最近10个交易日里 volume全是NULL或0 (>=8天)
c.execute("""
    SELECT k.code, si.code_name, COUNT(*) as null_days
    FROM kline_daily k
    LEFT JOIN stock_industry si ON k.code = si.code
    WHERE k.date >= '2026-04-20'
    AND (k.volume IS NULL OR k.volume = 0)
    GROUP BY k.code, si.code_name
    HAVING null_days >= 8
    ORDER BY null_days DESC
    LIMIT 20
""")
print('近期持续停牌股票(>=8天volume=0):')
for r in c.fetchall():
    print(' ', r)

# 总共有多少股停牌
c.execute("""
    SELECT COUNT(DISTINCT code) FROM kline_daily
    WHERE date >= '2026-04-01' AND (volume IS NULL OR volume = 0)
""")
print('4月以来有过停牌记录的股票数:', c.fetchone()[0])

# 历史上volume=NULL的分布
c.execute("""
    SELECT YEAR(date) as yr, COUNT(*) as cnt, COUNT(DISTINCT code) as stocks
    FROM kline_daily WHERE volume IS NULL OR volume = 0
    GROUP BY yr ORDER BY yr
""")
print('\n按年份-volume=NULL分布:')
for r in c.fetchall():
    print(' ', r)

# 最近有没有根本没有数据的股票(历史有数据、近期全没了=可能退市)
c.execute("""
    SELECT k.code, si.code_name, MAX(k.date) as last_date
    FROM kline_daily k
    LEFT JOIN stock_industry si ON k.code = si.code
    GROUP BY k.code, si.code_name
    HAVING last_date < '2026-03-01'
    ORDER BY last_date ASC
    LIMIT 20
""")
print('\n最后一条数据早于2026-03的股票(可能退市):')
for r in c.fetchall():
    print(' ', r)
c.execute("""
    SELECT COUNT(*) FROM (
        SELECT code FROM kline_daily
        GROUP BY code HAVING MAX(date) < '2026-03-01'
    ) t
""")
print('  合计:', c.fetchone()[0], '只')

conn.close()
