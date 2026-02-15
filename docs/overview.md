# CodeWiki Repository Overview

## Purpose

CodeWiki is an automated documentation generation system designed to transform code repositories into comprehensive, structured documentation using AI (Large Language Models). It bridges the gap between raw source code and human-readable documentation by analyzing code structure, mapping dependencies, and orchestrating AI agents to write detailed explanations.

The system supports multiple programming languages (Python, JavaScript, TypeScript, Java, C#, C, C++, PHP, Go) and provides both a Command Line Interface (CLI) for local development workflows and a Web Frontend for hosted services.

## End-to-End Architecture

The CodeWiki architecture is divided into three primary layers: **User Interfaces**, **Processing Backend**, and **Shared Foundations**.

```mermaid
graph TB
    subgraph "User Interfaces"
        CLI[CLI Application<br/>codewiki/cli]
        WEB[Web Frontend<br/>codewiki/src/fe]
    end

    subgraph "Processing Backend"
        direction TB
        AGENT[Agent Backend<br/>codewiki/src/be]
        DEP[Dependency Analyzer<br/>codewiki/src/be/dependency_analyzer]
    end

    subgraph "Shared Foundations"
        UTILS[Shared Utilities<br/>codewiki/src]
    end

    subgraph "External Systems"
        REPO[Source Repository]
        LLM[LLM Services]
        STORAGE[File System/Storage]
    end

    %% Interactions
    CLI -- "1. Initiate Job" --> AGENT
    WEB -- "1. Submit Repo" --> AGENT
    
    AGENT -- "2. Analyze Code" --> DEP
    DEP -- "3. Return Graph/AST" --> AGENT
    
    AGENT -- "4. Generate Docs" --> LLM
    LLM -- "5. Return Content" --> AGENT
    
    AGENT -- "6. Save Output" --> UTILS
    UTILS --> STORAGE
    
    %% Config flows
    UTILS -.-> CLI
    UTILS -.-> WEB
    UTILS -.-> AGENT
    
    REPO --> DEP

    %% Styling
    classDef interface fill:#e1f5fe,stroke:#01579b,stroke-width:2px;
    classDef backend fill:#fff3e0,stroke:#e65100,stroke-width:2px;
    classDef shared fill:#f3e5f5,stroke:#4a148c,stroke-width:2px;
    classDef external fill:#f5f5f5,stroke:#616161,stroke-width:2px,stroke-dasharray: 5 5;

    class CLI,WEB interface;
    class AGENT,DEP backend;
    class UTILS shared;
    class REPO,LLM,STORAGE external;
```

## Core Modules Documentation

The repository is organized into the following core modules:

- **[CLI Application](CLI%20Application.md)**: Provides the command-line interface for users to configure settings, validate repositories, and trigger documentation generation jobs locally.
- **[Dependency Analyzer](Dependency%20Analyzer.md)**: The analytical engine responsible for parsing source code, extracting ASTs (Abstract Syntax Trees), and building dependency graphs to understand code structure.
- **[Agent Backend](Agent%20Backend.md)**: The core processing unit that orchestrates AI agents. It uses the dependency graph to cluster code into logical modules and interacts with LLMs to generate documentation text.
- **[Web Frontend](Web%20Frontend.md)**: A FastAPI-based web application that allows users to submit GitHub repositories for documentation generation and view the results via a web interface.
- **[Shared Utilities](Shared%20Utilities.md)**: Contains foundational components like `Config` (configuration management) and `FileManager` (I/O operations) used across all other modules.