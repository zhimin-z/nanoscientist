"""
Minimal example of MSR-Scientist usage.
"""

from msr_scientist import create_agent

# Set HF_TOKEN in environment first:
# export HF_TOKEN=your_token_here

# Create agent
agent = create_agent()

# Research!
agent.research("""
Research Task: Compare the performance of bubble sort vs quicksort.

1. Formulate a hypothesis about their time complexity
2. Implement both algorithms
3. Run timing experiments with arrays of size 100, 1000, 10000
4. Analyze results
5. Draft a short research paper with your findings

Follow the cycle: PLAN → IMPLEMENT → VERIFY → SHIP
""")

print("\n✅ Check outputs/ directory for papers and results!")
