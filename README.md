# rosqa-generator

`rosqa-generator` is a command-line tool that parses **ROSpec** architectural specifications and automatically generates structured **question–answer pairs** for ROS 2 systems.

The tool is intended for architectural analysis and research. It systematically generates a **family of questions for every entity supported by ROSpec**, covering both static structure and dynamic communication semantics.

---

## What the tool does

Given a `.rospec` file, the tool:

- Parses the ROSpec specification into an internal graph model
- Extracts all architectural entities and relations, including:
  - Node types and node instances
  - Topics and services
  - Parameters and contexts
  - Dynamic `content(...)` services (service names resolved via parameters)
  - QoS policies and QoS attachments
  - Type aliases and message aliases
  - TF relations
  - Constraints and `where {}` blocks
- Generates multi-level question–answer pairs:
  - **Level 0 (ENTITY)**: entity existence and classification
  - **Level 1 (RELATION)**: relations, configuration, types, constraints, attachments
  - **Level 2 (PATH)**: end-to-end communication paths via topics and services
- Outputs the result as a JSON file

All answers are deterministically derived from the ROSpec input. No runtime ROS system is required.

---

## Installation

Clone the repository and install it in editable mode:

```bash
git clone https://github.com/YuqiYue/rosqa-generator.git
cd rosqa-generator
python -m venv venv
source venv/bin/activate
pip install -e .
```
This installs the rosqa command-line interface.

## CLI usage
The tool provides a single command-line entry point: `rosqa`.

Basic usage:

```bash
rosqa path/to/spec.rospec -o output.json
```

Example:
```bash
rosqa examples/laser_scan_matcher.rospec -o out/laser_scan_matcher.json
```

Batch processing of multiple ROSpec files:
```bash
for f in examples/*.rospec; do
  name=$(basename "$f" .rospec)
  rosqa "$f" -o "out/$name.json"
done
```

## Output format
The output is a JSON array. Each entry has the following structure:
```bash
{
  "level": 1,
  "category": "PARAMETER",
  "type": "OPEN",
  "question": "What is the type of parameter use_sim_time in node laser_scan_matcher?",
  "answer": "bool"
}
```

## Fields

- **level**
  - `0` – ENTITY  
  - `1` – RELATION  
  - `2` – PATH  

- **category**  
  Semantic category of the question, for example:
  - ENTITY, NODE, NODE_TYPE
  - TOPIC, SERVICE
  - PARAMETER, PARAMETER_ASSIGN
  - CONTEXT, CONTEXT_ASSIGN
  - QOS_POLICY, QOS_ATTACHMENT
  - TYPE_ALIAS, MESSAGE_ALIAS
  - CONTENT_SERVICE, CONTENT_TOPIC
  - TF, REMAP, WHERE_BLOCK
  - MESSAGE (for communication paths)

- **type**
  - `BOOL` – yes/no question
  - `MCQ` – multiple choice
  - `OPEN` – open-ended answer

- **question**  
  Natural-language question generated from the model

- **answer**  
  Ground-truth answer extracted from the ROSpec

---

## Notes and design choices

- ROSpec files **without a `system {}` block** are supported.  
  In such cases, only type-level questions are generated, while instance-level questions  
  (parameter assignments, remaps, communication paths) are omitted.

- Negative (non-existent) entities are optionally generated at **Level 0** to provide coverage  
  for entity-existence queries.

- Communication paths (**Level 2**) are computed by building a graph over:
  - topic publish–subscribe relations
  - service client–server relations
  - dynamically resolved `content(...)` services
  - remapped topics and services

- The tool operates purely on the ROSpec model and is independent of ROS 2 runtime APIs.


## Project structure
```text
src/rosqa/
├── cli.py              # Command-line interface
├── model.py            # Graph and entity data structures
├── rospec_loader.py    # ROSpec parser
├── questions.py        # Question generation logic
```

## Programmatic usage as a Python library

In addition to the CLI, **rosqa-generator** can be used directly as a Python library.  
This is useful if you want to integrate question generation into another analysis tool,
a pipeline, or a research prototype.

After installing the package in editable or normal mode:

```bash
pip install -e .
```
you can import and use the core APIs as follows.

### Basic usage
```python
from pathlib import Path
from rosqa.rospec_loader import load_graph_from_rospec
from rosqa.questions import generate_questions

# Load a ROSpec file into an internal graph model
graph = load_graph_from_rospec(Path("examples/laser_scan_matcher.rospec"))

# Generate all questions
questions = generate_questions(graph)

# Inspect questions
for q in questions[:5]:
    print(q.level, q.category, q.question, "=>", q.answer)
```

Each element in `questions` is a `Question` dataclass with the following attributes:

- **level**: abstraction level (`ENTITY`, `RELATION`, `PATH`)
- **category**: semantic category (e.g., `PARAMETER`, `SERVICE`, `CONTENT_SERVICE`)
- **qtype**: question type (`BOOL`, `MCQ`, `OPEN`)
- **question**: natural-language question
- **answer**: ground-truth answer derived from the ROSpec

### Customizing generation
The generator exposes optional parameters to control its behavior:

```python
questions = generate_questions(
    graph,
    include_negative_entities=True,
    negative_entities_per_file=5,
)
```

This allows you to:
- enable or disable negative (non-existent) entity questions
- control how many negative entities are generated per specification
