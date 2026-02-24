# Paper Quality Standard

A reference standard for what the autonomous scientist agent should produce.
Distilled from NeurIPS/ICML/ICLR/CVPR/ACL reviewer guidelines, Elsevier editorial
standards, PLOS "Ten Simple Rules", and established ML writing guides.

The document flows from **what reviewers evaluate** (Section 1) to **what counts as
novel** (Section 2) to **how to structure the paper** (Section 3) to **how to run
convincing experiments** (Section 4) to **how to write well** (Section 5) to
**technical requirements** (Sections 6-8) to **what causes rejection** (Section 9)
to **a final checklist** (Section 10).

---

## 1. Evaluation Framework

Reviewers at top venues assess papers on four dimensions. The agent should
target **3+ out of 4** on every axis.

| Dimension         | What reviewers ask |
|-------------------|--------------------|
| **Soundness**     | Are claims well-supported by evidence or proof? Are methods technically correct? Are assumptions stated and reasonable? |
| **Significance**  | Does the work advance understanding or practice? Will other researchers build on it? Does it open new directions? |
| **Originality**   | Does the paper provide new insights, methods, or perspectives? Is the contribution clearly differentiated from prior work? |
| **Clarity**       | Is the paper well-organized and clearly written? Could a competent researcher reproduce the work from the description alone? |

**Scoring context** (NeurIPS 2025, 1-6 scale): A score of 5 ("Accept") means
"technically solid with high impact on at least one sub-area." A score of 2
("Reject") means "technical flaws, weak evaluation, or inadequate
reproducibility." The agent should aim for content that would score 4-5.

---

## 2. Novelty and Contribution

### 2.1 What Counts as Novel

Novelty is **not** limited to entirely new methods. Top conferences recognize
multiple types of contribution:

| Contribution Type       | Example |
|-------------------------|---------|
| **New method/algorithm**    | A novel architecture, training procedure, or optimization technique |
| **New theoretical result**  | A tighter bound, a new proof, removing restrictive assumptions from prior theory |
| **New problem formulation** | Identifying and formalizing a previously unstudied problem |
| **New empirical finding**   | Demonstrating unexpected behavior, failure modes, or scaling laws |
| **New application**         | Applying known techniques to a new domain with domain-specific adaptations |
| **Bridge paper**            | Connecting ideas from disparate fields in a principled way |
| **Resource/benchmark**      | A new dataset, evaluation protocol, or reproducibility study |

**Key principle** (ICLR 2026): "A lack of state-of-the-art results does not
by itself constitute grounds for rejection." What matters is **insight**, not
just performance numbers.

### 2.2 How to Demonstrate Novelty

1. **State 1-3 specific claims** about what is new and why it matters.
2. **Differentiate explicitly**: include a "What's New" paragraph comparing
   with the closest prior work. Do not rely on reviewer ignorance of the field.
3. **Identify the type of contribution** (method, theory, empirical, application)
   and frame the paper accordingly.
4. **Articulate the insight**, not just the delta. "We add module X to
   architecture Y" is weak. "We show that property Z enables a 3x speedup
   because of mechanism W" is strong.

### 2.3 Novelty Pitfalls to Avoid

| Pitfall | Description | How to Avoid |
|---------|-------------|--------------|
| Incremental extension | Small modification without new insight | Frame around the insight, not the delta |
| Trivial combination | Combining known techniques without principled motivation | Justify theoretically or empirically why the combination works and when it fails |
| Benchmark chasing | Claiming novelty solely from SOTA numbers | Explain WHY the method works, not just that it does |
| Reformulation without insight | Rewriting an existing method in different notation | Show the reformulation enables new capabilities or analysis |
| Missing contextualization | Failing to position against the closest related work | Explicitly compare with the most similar existing approaches |

---

## 3. Structural Requirements

### 3.1 Mandatory Sections (IMRaD + Extensions)

Every report **must** contain these sections in order:

| Section | Purpose | Quality Criteria |
|---------|---------|------------------|
| **Title** | Capture the central contribution | Under 15 words. Specific and descriptive. Reader knows the topic and angle immediately. No jargon or acronyms. |
| **Abstract** | Self-contained summary (Context-Content-Conclusion) | 150-250 words. States the problem, approach, key findings, and significance. Independently understandable without reading the paper. |
| **Introduction** | Establish context, identify gap, state contribution | Progresses from broad field to specific gap. Ends with a clear statement of what this paper does and why it matters. States 1-3 specific claims. |
| **Background / Related Work** | Situate within existing literature | Synthesizes (not just summarizes) prior work. Groups by theme, not chronologically. Identifies what is missing. Positions each group relative to this paper's contribution. |
| **Methods** | Describe approach in reproducible detail | Sufficient detail for replication. States assumptions explicitly. References established methods rather than re-describing them. Justifies design choices. |
| **Results** | Present findings objectively | Declarative statements supported by data, figures, or tables. Reports metrics with variance (mean +/- std). Separates observation from interpretation. |
| **Discussion** | Interpret results, address limitations | Compares findings with prior work. Explains surprising results. Acknowledges weaknesses and failure modes honestly. Suggests future directions. |
| **Conclusion** | Synthesize contribution and implications | Not a repeat of the abstract. States how the work advances the field and what it enables. 1-2 paragraphs max. No speculation presented as conclusion. |
| **References** | Complete, accurate BibTeX | Every citation in text appears in references and vice versa. Correct author names, years, venues. |

### 3.2 Conditional Sections (include when relevant)

| Section | When to include |
|---------|-----------------|
| **Figures / Tables** | When data, architecture, or processes benefit from visual representation. Every figure must have a detailed, self-contained caption. |
| **Limitations** | Always encouraged. A dedicated subsection of Discussion or standalone. Missing this section is grounds for desk rejection at ACL. |
| **Broader Impact** | When the topic has societal implications. Required at NeurIPS. Discuss both positive and negative impacts. |
| **Appendix** | For proofs, extended results, hyperparameter tables, or supplementary details that would interrupt flow. |

### 3.3 Adaptive Structure by Report Type

The agent adapts report depth to the budget:

| Report Type | Budget Range | Sections to Include |
|-------------|-------------|---------------------|
| **Quick Summary** | < $0.10 | Title, Abstract, Introduction, Background, Conclusion, References |
| **Literature Review** | $0.10-$0.50 | All mandatory except Methods and Results |
| **Research Report** | $0.50-$2.00 | All mandatory sections + Limitations |
| **Full Paper** | $2.00+ | All mandatory + Figures/Tables + Limitations + Broader Impact + Appendix |

---

## 4. Experimentation Standards

This section applies to Research Reports and Full Papers (budget >= $0.50).
For literature reviews, experimentation criteria do not apply, but critical
assessment of other papers' experiments is expected.

### 4.1 Baselines

Every experiment must include at minimum:

| Baseline Type | Purpose |
|---------------|---------|
| **Simple/trivial baseline** | Sanity check (e.g., random, majority class, linear regression) |
| **Current SOTA or strongest published method** | Demonstrates improvement over the best known approach |
| **Closest related method** | Isolates the specific contribution of this work vs. the most similar prior approach |

All baselines must use **identical preprocessing, data splits, and evaluation
protocols**. Cherry-picked or inconsistent comparisons are a top rejection reason.

### 4.2 Ablation Studies

Prove that **each proposed component contributes** to the final result:

- **Component removal**: Systematically remove or replace each key component and
  measure impact.
- **Parameter sensitivity**: Vary key hyperparameters and show the method is
  robust (or characterize when it is not).
- **Scaling analysis**: If applicable, show performance vs. data size, model size,
  or compute.

### 4.3 Statistical Rigor

- **Report variance**: Mean +/- standard deviation over multiple runs (minimum 3,
  preferably 5+). Single-run results are insufficient.
- **Confidence intervals or significance tests**: For key comparisons, report
  whether differences are statistically significant.
- **Multiple metrics**: Do not rely on a single metric. For classification, include
  precision, recall, F1, not just accuracy. For generation, include multiple
  quality measures.
- **Both training and test metrics**: Show the model is not overfitting.

### 4.4 Reproducibility Requirements

- **Hyperparameters**: List all hyperparameters with search ranges and final
  selected values.
- **Data splits**: Specify exact train/val/test splits, random seeds.
- **Compute**: Report hardware type (GPU model), memory, and wall-clock time.
- **Code**: Reference code availability (or state that it will be released).

### 4.5 What Makes Experiments Unconvincing

| Problem | Impact |
|---------|--------|
| Missing baselines | Cannot assess whether improvement is meaningful |
| No ablations | Cannot attribute improvement to the proposed method |
| Single-run results | Results may be due to random seed |
| Unfair comparisons | Different preprocessing, data, or compute budgets across methods |
| Only accuracy reported | Hides failure modes visible in other metrics |
| No failure analysis | Reviewer cannot assess method limitations |

---

## 5. Writing Craft

### 5.1 Narrative Arc

A paper is **a story built around 1-3 specific claims**. Everything in the
paper exists to support this narrative. The ideal arc:

1. **Motivating problem**: Open with a concrete, compelling example or question.
2. **Gap**: Show what current approaches cannot do or do not explain.
3. **Key insight**: State the core idea that addresses the gap.
4. **Evidence**: Present experiments or theory that validate the claims.
5. **Implications**: Close with what this enables for the field.

**The first sentence is the most valuable real estate.** If it could be prepended
to any ML paper ("Deep learning has achieved great success..."), delete it and
write something specific.

### 5.2 Paragraph Structure (C-C-C Pattern)

Every paragraph follows **Context-Content-Conclusion**:
- **First sentence**: Sets context or connects to the previous paragraph.
- **Body**: Presents the new content, evidence, or argument.
- **Last sentence**: States the takeaway or transitions forward.

Keep paragraphs to 3-6 sentences, each discussing a single idea.

### 5.3 Claim-Evidence Structure

Throughout the paper, follow **Claim-Evidence-Commentary**:
1. State the claim.
2. Provide the evidence (data, citation, proof).
3. Comment on how the evidence supports the claim.

**If you are not 100% sure about a claim, do not make it.** It is far better
to omit a one-line boast than to have a reviewer flag overclaiming.

### 5.4 Related Work Positioning

Related work is not a bibliography dump. It should:
- **Organize by theme**, not chronologically.
- **Position each group** relative to this paper's contribution: "Unlike X which
  assumes A, we relax this assumption by..."
- **Confront potential objections**: If a reader might think "but method Y already
  does this," address it preemptively.
- **Identify the gap** that this paper fills.

### 5.5 Style Rules

| Rule | Rationale |
|------|-----------|
| **Active voice preferred** | "We propose X" not "X is proposed" |
| **Formal academic tone** | No colloquialisms, contractions, or casual phrasing |
| **Define terms on first use** | Minimize jargon; assume a non-specialist reader |
| **Consistent notation** | Same symbol means the same thing throughout |
| **Minimal abbreviations** | Define on first use, use sparingly (no more than a few per paper) |
| **Parallel structure** | Similar ideas use similar grammatical form |
| **Descriptive headings** | "Attention Reduces Computational Cost" not "Results" |
| **Transition sentences** | Begin each section with a sentence linking to the previous one |

### 5.6 What to Avoid

- Repeating the abstract in the conclusion
- Listing results without interpretation in Discussion
- Citing work not discussed in the text
- Overclaiming: "We prove X" when you only "provide evidence for X"
- Speculation presented as conclusions
- Generic first sentences that apply to any paper in the field
- Excessive self-citation without justification
- Figures or tables not referenced in the text

---

## 6. Citation Quality

### 6.1 Requirements

| Criterion | Standard |
|-----------|----------|
| **Minimum count** | 5 for quick summary, 10-15 for literature review, 20+ for full paper |
| **Recency** | At least 30% from the last 5 years (when the field permits) |
| **Breadth** | Citations from multiple research groups, not just one lab |
| **Foundational** | Include seminal/foundational papers for the topic |
| **Completeness** | Every BibTeX entry must have: author, title, year, venue |
| **Consistency** | Every \cite{key} in text resolves; every .bib entry is cited |

### 6.2 BibTeX Entry Templates

```bibtex
@article{authorYYYYkeyword,
  title   = {Full title},
  author  = {Last, First and Last, First},
  journal = {Full journal name},
  volume  = {N},
  year    = {YYYY}
}
```

```bibtex
@inproceedings{authorYYYYkeyword,
  title     = {Full title},
  author    = {Last, First and Last, First},
  booktitle = {Proceedings of Conference Name},
  year      = {YYYY}
}
```

---

## 7. Ethics and Responsible Research

### 7.1 Limitations Section

Every paper should include an honest limitations section covering:
- **Scope constraints**: What the method does not address.
- **Assumptions**: Conditions under which the results hold (and when they may not).
- **Failure modes**: Observed cases where the method underperforms.
- **Data limitations**: Biases, coverage gaps, or domain restrictions in the data.

**Honesty is rewarded.** Reviewers are instructed to reward rather than punish
upfront acknowledgment of limitations.

### 7.2 Broader Impact

When applicable (required for NeurIPS, encouraged everywhere):
- Discuss **both positive and negative** societal implications.
- Consider fairness, bias, privacy, environmental cost (compute), and affected
  communities.
- For high-risk models (generative models, pretrained LLMs), discuss safeguards:
  content filters, usage guidelines, potential misuse scenarios.
- If the work is purely theoretical with no foreseeable societal impact, state this
  explicitly rather than leaving the section empty.

### 7.3 Reproducibility Ethics

- Disclose all use of LLMs in the research methodology.
- Make code and data available (or commit to release).
- Report negative results alongside positive ones.
- Do not suppress inconvenient findings.

---

## 8. LaTeX Quality

### 8.1 Compilation Requirements

- **Zero errors**: The .tex file must compile via `pdflatex + bibtex + pdflatex + pdflatex`
- **Zero missing references**: No `[?]` in the compiled PDF
- **Zero undefined citations**: Every `\cite{key}` resolves to a bibliography entry
- **Clean log**: Overfull hbox warnings are acceptable; undefined references are not

### 8.2 Formatting Standards

- 11pt font, 1-inch margins (article class)
- Consistent heading hierarchy (`\section`, `\subsection`, `\subsubsection`)
- Tables use `booktabs` (`\toprule`, `\midrule`, `\bottomrule`)
- Mathematics in proper LaTeX: `$x$` for inline, `\begin{equation}` for display
- Cross-references via `\ref{}` and `\label{}`

---

## 9. Common Rejection Reasons

Understanding why papers get rejected helps the agent avoid these pitfalls.

### 9.1 Technical / Methodological

| Rejection Reason | Description |
|------------------|-------------|
| **Technical flaws** | Incorrect proofs, unsound methodology, invalid experimental design |
| **Claims exceed evidence** | Conclusions stronger than what the data supports |
| **Weak evaluation** | Insufficient baselines, missing ablations, no statistical significance |
| **Poor reproducibility** | Missing hyperparameters, no code, insufficient detail |
| **Unfair comparisons** | Inconsistent preprocessing, cherry-picked baselines |

### 9.2 Novelty / Significance

| Rejection Reason | Description |
|------------------|-------------|
| **Insufficient novelty** | Incremental extension without new insight |
| **Missing related work** | Failing to cite or compare with closest prior work |
| **Limited impact** | Work does not advance understanding or open new directions |
| **Trivial results** | Results that follow obviously from known techniques |

### 9.3 Presentation

| Rejection Reason | Description |
|------------------|-------------|
| **Unclear writing** | Vague terminology, undefined notation, unclear research question |
| **Poor organization** | Missing logical flow, hard-to-follow argumentation |
| **Overclaiming** | Misleading framing or exaggerated significance |
| **Speculation as conclusions** | Drawing conclusions not supported by evidence |
| **Missing limitations** | No honest discussion of what the method cannot do |

---

## 10. Self-Assessment Checklist

Before finalizing, the agent should verify every item.

### Novelty and Contribution
- [ ] 1-3 specific claims are stated in the introduction
- [ ] Each claim is differentiated from the closest prior work
- [ ] The type of contribution is identified (method, theory, empirical, application)
- [ ] The core insight is articulated, not just the technical delta

### Experimentation (if applicable)
- [ ] Simple baseline, strong baseline, and closest-method baseline included
- [ ] Ablation study covers each key proposed component
- [ ] Results reported as mean +/- std over multiple runs
- [ ] Hyperparameters documented with search ranges and final values
- [ ] Compute requirements disclosed (hardware, time, memory)
- [ ] Both training and test metrics reported

### Structure and Content
- [ ] Title is specific, under 15 words, captures the contribution
- [ ] Abstract is self-contained, 150-250 words, follows C-C-C
- [ ] Introduction progresses: broad context -> gap -> contribution -> claims
- [ ] Background synthesizes by theme and identifies what is missing
- [ ] Every claim in the body is supported by citation or evidence
- [ ] Limitations are honestly acknowledged (scope, assumptions, failure modes)
- [ ] Conclusion does not repeat the abstract; states field advancement

### Writing Quality
- [ ] First sentence is specific and compelling, not generic
- [ ] Narrative follows claim-evidence-commentary pattern
- [ ] Related work organized by theme, positioned against this contribution
- [ ] Formal academic tone throughout; active voice preferred
- [ ] Technical terms defined on first use
- [ ] Consistent notation and terminology
- [ ] All figures and tables referenced in text with self-contained captions
- [ ] No overclaiming or speculation presented as fact

### Technical Compliance
- [ ] All citations in text match entries in .bib file
- [ ] All .bib entries are cited in the text
- [ ] BibTeX entries have complete metadata (author, title, year, venue)
- [ ] LaTeX compiles without errors
- [ ] No `[?]` markers in compiled output

### Ethics
- [ ] Limitations section included
- [ ] Broader impact discussed (if applicable)
- [ ] LLM usage disclosed (if applicable)

---

## Sources

- [NeurIPS 2025 Reviewer Guidelines](https://neurips.cc/Conferences/2025/ReviewerGuidelines)
- [NeurIPS 2024 Reviewer Guidelines](https://neurips.cc/Conferences/2024/ReviewerGuidelines)
- [NeurIPS Paper Checklist](https://neurips.cc/public/guides/PaperChecklist)
- [ICLR 2026 Reviewer Guide](https://iclr.cc/Conferences/2026/ReviewerGuide)
- [ICML 2025 Reviewer Instructions](https://icml.cc/Conferences/2025/ReviewerInstructions)
- [ICML 2002 Crafting Papers on Machine Learning](https://icml.cc/Conferences/2002/craft.html)
- [CVPR 2025 Reviewer Guidelines](https://cvpr.thecvf.com/Conferences/2025/ReviewerGuidelines)
- [ACL Rolling Review Reviewer Guidelines](http://aclrollingreview.org/reviewerguidelines)
- [Ten Simple Rules for Structuring Papers (PLOS)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5619685/)
- [11 Steps to Structuring a Science Paper (Elsevier)](https://www.elsevier.com/connect/11-steps-to-structuring-a-science-paper-editors-will-take-seriously)
- [Literature Review as Research Methodology (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S0148296319304564)
- [Ten Simple Rules for Writing a Literature Review (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC3715443/)
- [USC Literature Review Guide](https://libguides.usc.edu/writingguide/literaturereview)
- [Duke Scientific Writing Sections Guide](https://guides.mclibrary.duke.edu/scientificwriting/sections)
- [ICML 2024 Paper Guidelines](https://icml.cc/Conferences/2024/PaperGuidelines)
- [Best Practices for ML Experimentation](https://arxiv.org/html/2511.21354v2)
- [Highly Opinionated Advice on How to Write ML Papers](https://www.alignmentforum.org/posts/eJGptPbbFPZGLpjsp/highly-opinionated-advice-on-how-to-write-ml-papers)
