# Zim file explorer

An interactive command line tool for exploring links between articles in a zim file. Enter a keyword to search for an article, or enter an article's ID to see a complete list of forward links (links *from* the selected article, to other articles) and backward links (links *to* the selected article, from other articles).

## Usage

The provided Pipfile describes a Python environment in which this program can run.

    pipenv install
    pipenv shell
    python3 explore-zim.py /path/to/a/zim/file.zim

## Authors

Dylan McCall <dylan@endlessos.org>
