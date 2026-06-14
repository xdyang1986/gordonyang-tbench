import subprocess


def test_hello_world():
    result = subprocess.run(
        ["python3", "/app/hello.py", "World"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert "Hello, World! The answer is 520." in result.stdout, (
        f"Wrong output: {result.stdout}"
    )


def test_hello_alice():
    result = subprocess.run(
        ["python3", "/app/hello.py", "Alice"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    expected_sum = sum(ord(c) for c in "Alice")  # 65+108+105+99+101 = 478
    assert f"Hello, Alice! The answer is {expected_sum}." in result.stdout, (
        f"Wrong output: {result.stdout}"
    )
