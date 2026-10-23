import os, sys, asyncio
sys.path.insert(0, '/app')
print('env DSN?', bool(os.environ.get('DB_DSN')), flush=True)
import asyncpg
print('asyncpg imported', flush=True)
async def m():
    print('creating pool...', flush=True)
    p = await asyncpg.create_pool(dsn=os.environ['DB_DSN'], min_size=1, max_size=4)
    print('pool ok', flush=True)
    r = await p.fetchval('select count(*) from course_kb')
    print('count', r, flush=True)
    await p.close()
asyncio.run(m())
print('done', flush=True)
