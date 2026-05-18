# save as test_llm.py
import sys
sys.path.insert(0, '.')
from abt.llm_client import call_llm

try:
    result = call_llm(
        system="You are a helpful assistant.",
        user="Say hello in one sentence."
    )
    print("SUCCESS:", result)
except Exception as e:
    print("FAILED:", e)