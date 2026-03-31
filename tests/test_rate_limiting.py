import asyncio
import time
import pytest
from pydantic_core import Url
from mywbooks.async_download_manager import AsyncDownloadManager

@pytest.mark.asyncio
async def test_download_manager_cooldown():
    # Cooldown of 0.2s, max concurrent 5 (so concurrency won't be the limit)
    dm = AsyncDownloadManager(default_cooldown=0.2, max_concurrent_per_host=5)
    url = Url("https://httpbin.org/delay/0") # fast endpoint
    
    start_time = asyncio.get_event_loop().time()
    
    # Run 3 requests concurrently
    # Request 1: starts at 0s
    # Request 2: starts at 0.2s
    # Request 3: starts at 0.4s
    await asyncio.gather(
        dm.get_data(url, ignore_cache=True),
        dm.get_data(url, ignore_cache=True),
        dm.get_data(url, ignore_cache=True)
    )
    
    end_time = asyncio.get_event_loop().time()
    total_time = end_time - start_time
    
    # Should take at least 0.4s + network time
    assert total_time >= 0.4
    await dm.close()

@pytest.mark.asyncio
async def test_download_manager_concurrency():
    # Cooldown 0 (no gap), but max 2 concurrent
    # Use a slow endpoint to ensure they overlap
    dm = AsyncDownloadManager(default_cooldown=0, max_concurrent_per_host=2)
    url = Url("https://httpbin.org/delay/1") 
    
    start_time = asyncio.get_event_loop().time()
    
    # Run 3 requests
    # 1 & 2 start immediately and take ~1s
    # 3 waits for one of them to finish, then starts at ~1s and takes ~1s
    await asyncio.gather(
        dm.get_data(url, ignore_cache=True),
        dm.get_data(url, ignore_cache=True),
        dm.get_data(url, ignore_cache=True)
    )
    
    end_time = asyncio.get_event_loop().time()
    total_time = end_time - start_time
    
    # Should take at least 2s + network overhead
    assert total_time >= 2.0
    await dm.close()

@pytest.mark.asyncio
async def test_different_domains_no_blocking():
    # Long cooldown but shouldn't affect different hosts
    dm = AsyncDownloadManager(default_cooldown=1.0)
    url1 = Url("https://httpbin.org/get")
    url2 = Url("https://google.com")
    
    start_time = asyncio.get_event_loop().time()
    
    # These should run almost in parallel because they are different hosts
    await asyncio.gather(
        dm.get_data(url1, ignore_cache=True),
        dm.get_data(url2, ignore_cache=True)
    )
    
    end_time = asyncio.get_event_loop().time()
    total_time = end_time - start_time
    
    # Should be fast (< 1s) despite 1s cooldown
    assert total_time < 1.0
    await dm.close()
