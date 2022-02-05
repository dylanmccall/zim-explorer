#!/usr/bin/python3

from functools import partial
from urllib.parse import urlsplit
import bs4
import click
import itertools
import networkx as nx
import typing
from libzim.reader import Archive
from libzim.reader import Entry
from libzim.search import Searcher
from libzim.search import Query

from multiprocessing import Pool


ARTICLES_GRAPH_CHUNK_SIZE = 500

ARTICLES_NODE = "_ARTICLES"


@click.command()
@click.argument("zim_path", type=click.Path(readable=True))
def main(zim_path):
    zim = Archive(zim_path)

    all_entry_ids = range(zim.all_entry_count)
    all_entry_id_groups = list(grouper(all_entry_ids, ARTICLES_GRAPH_CHUNK_SIZE))
    graphs_count = len(all_entry_id_groups)

    with Pool() as pool:
        graphs = pool.imap_unordered(
            partial(zim_entries_to_graph, zim_path),
            all_entry_id_groups
        )
        with click.progressbar(label="Reading articles", iterable=graphs, length=graphs_count) as graphs_progress:
            graph = nx.compose_all(graphs_progress)

    articles_graph = graph.subgraph(
        graph.successors(ARTICLES_NODE)
    )

    while True:
        click.echo("Graph contains {count} articles".format(
            count=click.style(f"{articles_graph.order()}", bold=True)
        ))
        article_id = prompt_for_article_id(graph, zim)

        if article_id:
            print_article_details(articles_graph, article_id)


def print_article_details(graph: nx.DiGraph, article_id: str):
    article_data = graph.nodes.get(article_id)

    if not article_data:
        click.echo(
            "{warning} Article is not in the link graph".format(
                warning=click.style("Warning:", fg="red", bold=True)
            ),
            err=True
        )
        return
        
    click.echo()
    click.secho(article_data.get("title"), fg="white")
    click.secho(article_data.get("mimetype"), dim=True)
    click.echo()

    forward_links_list = [
        format_article_link(graph, node_id)
        for node_id in graph.successors(article_id)
        if node_id != ARTICLES_NODE and node_id != article_id
    ]

    backward_links_list = [
        format_article_link(graph, node_id)
        for node_id in graph.predecessors(article_id)
        if node_id != ARTICLES_NODE and node_id != article_id
    ]

    click.echo("Forward links:")
    for link_str in forward_links_list:
        click.echo(f" * {link_str}")
    if not forward_links_list:
        click.secho("(No forward links)", dim=True)

    click.echo()

    click.echo("Backward links:")
    for link_str in backward_links_list:
        click.echo(f" * {link_str}")
    if not backward_links_list:
        click.secho("(No backward links)", dim=True)

    click.echo()


def format_article_link(graph: nx.DiGraph, node_id: str) -> str:
    node_data = graph.nodes.get(node_id)
    node_title = node_data.get("title")
    if node_title:
        return "{id} {title}".format(
            id=click.style(f"{node_id}"),
            title=click.style(f"({node_title})", dim=True)
        )
    else:
        return "{id}".format(
            id=click.style(f"{node_id}")
        )


def prompt_for_article_id(graph: nx.DiGraph, zim: Archive) -> typing.Optional[str]:
    article_id = click.prompt("Enter an article")

    if graph.has_node(article_id):
        return article_id

    searcher = Searcher(zim)
    query = Query().set_query(article_id)
    search = searcher.search(query)

    search_articles = list(search.getResults(0, 10))

    click.echo("\nMatching articles:")
    for search_article_id in search_articles:
        click.echo(" * {article_id}".format(
            article_id=click.style(f"{search_article_id}", fg="white")
        ))
    click.echo()
    
    return None


def zim_entries_to_graph(zim_path: str, entry_ids: typing.Iterable[int]) -> nx.Graph:
    zim = Archive(zim_path)

    graph = nx.DiGraph()
    graph.add_node(ARTICLES_NODE)

    for entry_id in filter(lambda x: x is not None, entry_ids):
        entry = zim._get_entry_by_id(entry_id)
        add_zim_entry_to_graph(graph, entry)

    return graph


def add_zim_entry_to_graph(graph: nx.DiGraph, entry: Entry):
    if entry.is_redirect:
        graph.add_node(entry.path, title=entry.title)
        graph.add_edge(entry.path, entry.get_redirect_entry().path)
        return

    item = entry.get_item()

    if item.mimetype not in ["text/html"]:
        return

    graph.add_node(entry.path, title=entry.title, mimetype=item.mimetype)
    graph.add_edge(ARTICLES_NODE, entry.path)

    article_bytes = item.content.tobytes()
    soup = bs4.BeautifulSoup(article_bytes, "lxml")

    link_elems_list = soup.find_all("a")

    for link_elem in link_elems_list:
        if link_elem.has_attr("href"):
            url = link_elem['href'].lstrip("./")
            split_result = urlsplit(url)
            if split_result.netloc:
                # Ignore external links
                pass
            elif split_result.path:
                graph.add_edge(entry.path, url)


def grouper(iterable, n, *, incomplete='fill', fillvalue=None):
    "Collect data into non-overlapping fixed-length chunks or blocks"
    # grouper('ABCDEFG', 3, fillvalue='x') --> ABC DEF Gxx
    # grouper('ABCDEFG', 3, incomplete='strict') --> ABC DEF ValueError
    # grouper('ABCDEFG', 3, incomplete='ignore') --> ABC DEF
    # From <https://docs.python.org/3/library/itertools.html>
    args = [iter(iterable)] * n
    if incomplete == 'fill':
        return itertools.zip_longest(*args, fillvalue=fillvalue)
    if incomplete == 'strict':
        return zip(*args, strict=True)
    if incomplete == 'ignore':
        return zip(*args)
    else:
        raise ValueError('Expected fill, strict, or ignore')


if __name__ == '__main__':
    main()