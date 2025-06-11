# BUOYO Project Guidelines

## About Buoyo
Buoyo is an AI-powered semantic search and analysis tool for Twitter/X bookmarks. It allows users to scrape, store, search, and analyze their Twitter bookmarks using OpenAI embeddings and GPT-4o vision capabilities.

## Project Architecture
- **Data Collection**: JavaScript scraper (`bookmark-scraper-v2.js`) for Twitter bookmarks
- **Processing Engine**: Python scripts for data processing and AI analysis
- **Web Interface**: Flask app with Bootstrap UI for search and management
- **Database**: SQLite with embeddings for semantic search
- **AI Integration**: OpenAI embeddings (ada-002) and GPT-4o for image analysis

## Commands
- **Run web app**: `python app.py`
- **Run processing**: `python main.py`
- **Install dependencies**: `uv install` or `pip install -e .`
- **Format code**: `black .`
- **Type check**: `mypy .`

## Key Components
- **`app.py`**: Flask web interface with search, topics, and data management
- **`main.py`**: Core data processing and database operations
- **`tweet_processor.py`**: Background task processing for tweet imports
- **`image_analysis.py`**: GPT-4o integration for analyzing tweet images
- **`topic_analysis.py`**: K-means clustering for topic discovery
- **`task_manager.py`**: Background task management with progress tracking

## Data Flow
1. **Collection**: Run scraper in browser console → save JSON files to `data/`
2. **Processing**: Import tweets → generate embeddings → download images
3. **Analysis**: Analyze images with GPT-4o → cluster topics
4. **Search**: Semantic search using embeddings + image descriptions

## Development Workflow
1. Use scraper to collect bookmark data from Twitter
2. Process JSON files through web interface
3. Run image analysis for visual content search
4. Use topic analysis to discover conversation themes

## Style Guidelines
- Use Python 3.12+ features
- Import order: standard library → third-party → local modules
- Function naming: snake_case
- Variable naming: snake_case
- Class naming: PascalCase
- Constants: UPPER_CASE
- Docstrings: Google style
- Line length: 88 characters max (Black default)
- Use try/except for error handling with specific exceptions
- Prefer f-strings for string formatting
- Type hints required for function parameters and return values

## Dependencies
- **Flask 3.0.0**: Web framework
- **OpenAI 1.64.0**: AI integration (embeddings + GPT-4o)
- **scikit-learn 1.3.0**: Machine learning for clustering
- **NumPy 2.2.3**: Numerical operations
- **Requests 2.32.3**: HTTP requests