"""Quick test of basic functionality."""

from msr_scientist.tools import ToolRegistry
from msr_scientist.latex_writer import LatexPaperWriter
from msr_scientist.executor import Executor

# Test tool registry
print("Testing tool registry...")
tr = ToolRegistry()

def test_tool(x):
    return x * 2

tr.register_tool('double', test_tool)
result = tr.get_tool('double')(5)
print(f"  Tool test: double(5) = {result}")
assert result == 10, "Tool failed"

# Test executor
print("\nTesting executor...")
executor = Executor()
result = executor.execute_bash("echo 'Bash works'")
print(f"  Bash output: {result['stdout'].strip()}")
assert result['success'], "Bash execution failed"

# Test Python execution
result = executor.execute_python("x = 2 + 2\nprint(x)")
print(f"  Python output: {result['stdout'].strip()}")
assert "4" in result['stdout'], "Python execution failed"

# Test LaTeX writer
print("\nTesting LaTeX writer...")
latex = LatexPaperWriter(executor.workspace)
tex_file = latex.generate_paper(
    title="Test Paper",
    authors=["Test Author"],
    abstract="This is a test abstract.",
    sections=[
        {"title": "Introduction", "content": "Test content."}
    ]
)
print(f"  LaTeX file generated: {tex_file}")
assert tex_file.endswith(".tex"), "LaTeX generation failed"

print("\n✓ All basic tests passed!")
