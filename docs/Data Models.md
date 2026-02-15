# Data Models Module

## Overview

The **Data Models** module provides the foundational data structures used throughout the Dependency Analyzer system. It defines the core domain models that represent code elements, their relationships, and repository analysis results.

This module serves as the data layer for the entire dependency analysis pipeline, providing type-safe, validated structures using Pydantic models.

## Architecture

```mermaid
graph TB
    subgraph "Data Models Module"
        subgraph "Core Models"
            Node[Node<br/>Code Element Representation]
            Repository[Repository<br/>Repository Metadata]
            CallRelationship[CallRelationship<br/>Function Call Links]
        end
        
        subgraph "Analysis Models"
            AnalysisResult[AnalysisResult<br/>Complete Analysis Output]
            NodeSelection[NodeSelection<br/>Partial Export Selection]
        end
    end
    
    subgraph "Consumers"
        AnalysisService[Analysis Services]
        LanguageAnalyzers[Language Analyzers]
        DependencyGraph[Dependency Graph Builder]
    end
    
    Node --> AnalysisResult
    CallRelationship --> AnalysisResult
    Repository --> AnalysisResult
    NodeSelection -.->|Filters| Node
    
    AnalysisService --> AnalysisResult
    LanguageAnalyzers --> Node
    LanguageAnalyzers --> CallRelationship
    DependencyGraph --> Node
    DependencyGraph --> CallRelationship
    
    style Node fill:#e1f5ff
    style Repository fill:#e1f5ff
    style CallRelationship fill:#e1f5ff
    style AnalysisResult fill:#fff4e1
    style NodeSelection fill:#fff4e1
```

## Module Structure

The Data Models module is organized into two primary sub-modules:

### 1. [Core Models](data_models_core_models.md)
**Purpose:** Define fundamental domain entities for code representation

**Key Components:**
- **Node**: Represents code elements (functions, classes, methods) with metadata
- **Repository**: Stores repository identification and location information
- **CallRelationship**: Captures function/method call relationships

**File:** `codewiki/src/be/dependency_analyzer/models/core.py`

### 2. [Analysis Models](Data Models - Analysis Models.md)
**Purpose:** Structure analysis results and selection criteria

**Key Components:**
- **AnalysisResult**: Complete output of repository analysis containing all discovered nodes and relationships
- **NodeSelection**: Configuration for selective node export and filtering

**File:** `codewiki/src/be/dependency_analyzer/models/analysis.py`

## Data Flow

```mermaid
sequenceDiagram
    participant Analyzer as Language Analyzers
    participant Core as Core Models
    participant Graph as Dependency Graph Builder
    participant Analysis as Analysis Models
    participant Service as Analysis Service
    
    Analyzer->>Core: Create Node objects
    Analyzer->>Core: Create CallRelationship objects
    Core->>Graph: Provide nodes & relationships
    Graph->>Analysis: Build AnalysisResult
    Service->>Analysis: Query AnalysisResult
    Analysis-->>Service: Return complete analysis data
```

## Key Features

### Type Safety & Validation
All models use **Pydantic** for:
- Automatic type validation
- JSON serialization/deserialization
- IDE autocompletion support
- Schema enforcement

### Flexibility
- **Optional fields**: Many fields are optional to accommodate different programming languages
- **Extensible metadata**: `Dict` and `Any` types allow custom attributes
- **Set-based dependencies**: Efficient dependency tracking using sets

### Integration Points

This module is consumed by:

| Module | Usage |
|--------|-------|
| [Analysis Services](Dependency Analyzer.md#analysis-services) | Create and populate AnalysisResult objects |
| [Language Analyzers](Dependency Analyzer.md#language-analyzers) | Generate Node and CallRelationship instances |
| [Dependency Graph Builder](Dependency Analyzer.md#core-graph-processing) | Build graphs from Node collections |
| [CLI Application](CLI Application.md) | Serialize results for output |

## Design Principles

1. **Immutability by Default**: Pydantic models are immutable unless explicitly configured
2. **Explicit Relationships**: Clear distinction between identity (`id`) and display (`name`, `display_name`)
3. **Language Agnostic**: Models support multiple programming languages through optional fields
4. **Traceability**: Every element includes source location (`file_path`, `start_line`, `end_line`)

## Dependencies

```mermaid
graph LR
    Analysis[Analysis Models] --> Core[Core Models]
    Core --> Pydantic[Pydantic Library]
    
    style Pydantic fill:#f0f0f0
```

**External Dependencies:**
- `pydantic`: Data validation and serialization
- `datetime`: Timestamp support
- `typing`: Type hints and generics

## Related Documentation

- **Parent Module:** [Dependency Analyzer](Dependency Analyzer.md)
- **Consumers:** [Language Analyzers](Dependency Analyzer.md#language-analyzers), [Analysis Services](Dependency Analyzer.md#analysis-services)
- **Configuration:** [CLI Application](CLI Application.md) - Uses these models for job configuration
