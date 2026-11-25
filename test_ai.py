
from pathlib import Path
from ai_reader import read_source, ask_ollama, normalize

file = Path("bang_gia_test.png")

print("AI dang doc bang gia...")

text, images, warnings = read_source(
    file.name, file.read_bytes()
)

result = ask_ollama(
    text, images, model="qwen2.5vl:3b"
)

fees, warnings2 = normalize(result)

print("\n=== KET QUA AI ===")
print(fees.to_string(index=False))

print("\n=== CANH BAO ===")
for w in warnings + warnings2:
    print("-", w)
