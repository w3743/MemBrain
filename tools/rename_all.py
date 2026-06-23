"""批量替换项目中所有命名引用。"""
import os, re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 替换规则：(模式, 替换为)，按顺序应用
RULES = [
    # Python 包导入
    ('membrain.', 'membrain.'),
    ('membrain/', 'membrain/'),
    ('membrain"', 'membrain"'),
    ("membrain'", "membrain'"),
    # pip 包名
    ('membrain', 'membrain'),
    ('membrain', 'membrain'),  # 剩余的纯 membrain
    # 类脑记忆文案（保留 CSM 为模块内部实现名，仅改面向用户的）
    # CSM 类名/变量名保持不动（内部实现）
    # 以下只改面向用户的文案
]

SKIP_DIRS = {'.git', '__pycache__', 'models', '.pytest_cache', '.pytest_tmp', '.tmp', '.csm_eval', 'membrain.egg-info'}
SKIP_EXTS = {'.pyc', '.db', '.db-wal', '.db-shm', '.pdf', '.safetensors', '.bin', '.pth'}

def process_file(filepath):
    ext = os.path.splitext(filepath)[1]
    if ext in SKIP_EXTS:
        return False
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except:
        return False
    
    modified = content
    # 1. membrain → membrain (Python import)
    modified = modified.replace('membrain', 'membrain')
    # 2. membrain → membrain (pip name)
    modified = modified.replace('membrain', 'membrain')
    # 3. membrain_memory → membrain_memory (DB/file paths)
    modified = modified.replace('membrain_memory', 'membrain_memory')
    # 4. mb-memory → mb-memory (pi extension)
    modified = modified.replace('mb-memory', 'mb-memory')
    # 5. MB 记忆 → MB 记忆（中文文案）
    modified = modified.replace('MB 记忆', 'MB 记忆')
    modified = modified.replace('MB 记忆', 'MB 记忆')
    
    if modified != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(modified)
        return True
    return False

count = 0
for root, dirs, files in os.walk(BASE):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for fname in files:
        fp = os.path.join(root, fname)
        if process_file(fp):
            count += 1
            print(f'  modified: {os.path.relpath(fp, BASE)}')

print(f'\n{count} files modified')
