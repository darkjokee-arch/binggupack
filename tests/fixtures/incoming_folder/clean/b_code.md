## 코드블록 보존

아래 코드는 내부 빈 줄이 있어도 한 블록으로 보존되어야 한다.

```python
def adapt(input_dirs):
    files = scan_markdown_files(input_dirs)

    return redact_and_validate(files)
```

코드 다음 단락은 별개 블록이다.
