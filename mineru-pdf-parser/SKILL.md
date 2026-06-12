---
name: mineru-pdf-parser
description: 此 skill 在用户需要解析 PDF 或图片文件提取文本内容时使用。调用 MinerU VLM 模型服务将文档转为 Markdown，自动处理大文件分页拆分。
---

# MinerU PDF 解析

## 触发条件
- 用户要求解析/提取/读取 PDF 或图片内容
- 用户要求将 PDF 转为文本/Markdown

## API 端点
- 测试环境：`http://172.21.27.32:38000/file_parse`
- OA 环境：`http://10.32.33.192:9000/file_parse`

## 调用方式
```
POST /file_parse
Content-Type: multipart/form-data
参数：files=文件, backend=vlm-vllm-async-engine
```

## 返回格式
```json
{ "backend": "...", "version": "...", "results": { "{filename}": { "md_content": "markdown文本" } } }
```

## 核心逻辑

### 1. 小文件（≤5页）直接调用
```python
import requests
resp = requests.post(API_URL,
    files={'files': open(pdf_path, 'rb')},
    data={'backend': 'vlm-vllm-async-engine'}, timeout=600)
md = list(resp.json()['results'].values())[0]['md_content']
```

### 2. 大文件（>5页）先拆分再逐块调用
```python
from PyPDF2 import PdfReader, PdfWriter
import requests, time, tempfile, os

reader = PdfReader(pdf_path)
total = len(reader.pages)
temp_dir = tempfile.mkdtemp()
md_parts = []

for start in range(0, total, 5):
    end = min(start + 5, total)
    # 拆分
    writer = PdfWriter()
    for i in range(start, end):
        writer.add_page(reader.pages[i])
    chunk_path = os.path.join(temp_dir, f'chunk_{start+1}_{end}.pdf')
    with open(chunk_path, 'wb') as f:
        writer.write(f)
    # 调用API
    with open(chunk_path, 'rb') as f:
        resp = requests.post(API_URL,
            files={'files': (os.path.basename(chunk_path), f, 'application/pdf')},
            data={'backend': 'vlm-vllm-async-engine'}, timeout=600)
    result = resp.json()
    for val in result.get('results', {}).values():
        md = val.get('md_content', '')
        if md:
            md_parts.append(f'\n\n--- 页 {start+1}-{end} ---\n\n{md}')
    time.sleep(1)

# 合并输出
full_md = ''.join(md_parts)
import shutil; shutil.rmtree(temp_dir)
```

### 3. 合并输出
将所有块的 `md_content` 拼接，保存为 `full_parsed.md`

## 注意事项
- 每次调用文件必须 ≤5 页，否则内存暴涨导致失败
- 超时建议 600 秒
- 支持 PDF 和图片（jpg/png）
- API 可用性验证：`curl -kv {URL}/docs`
- 模型：MinerU2.5-2509-1.2B，基于 VLM 的文档解析
- GitHub：https://github.com/opendatalab/MinerU
