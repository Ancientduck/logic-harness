import os
import chardet

def read_file(path):
    if not os.path.exists(path):
        return f"Error: File not found: {path}"
    try:
        with open(path, "rb") as f:
            raw = f.read()
        detected = chardet.detect(raw)
        enc = detected["encoding"] or "utf-8"
        return raw.decode(enc, errors="replace")
    except Exception as e:
        return f"Error reading file: {e}"

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: read_file.py <path>")
    else:
        print(read_file(sys.argv[1]))
