#!/usr/bin/python3

from functools import partial
from urllib.parse import urlsplit
import bs4
import click
import itertools
import networkx as nx
import sys
import typing
from libzim.reader import Archive
from libzim.reader import Entry
from libzim.search import Searcher
from libzim.search import Query

from multiprocessing import Pool


ALL_ARTICLES_GRAPH_CHUNK_SIZE = 500

ARTICLES_NODE = "_ARTICLES"
CATEGORIES_NODE = "_CATEGORIES"
SPECIAL_NODES = [ARTICLES_NODE, CATEGORIES_NODE]


class ZimExplorerContext(object):
    __zim_path: str
    __exclude_related: bool

    __zim: typing.Optional[Archive] = None
    __full_graph: typing.Optional[nx.DiGraph] = None

    def __init__(self, zim_path: str, exclude_related: str):
        self.__zim_path = zim_path
        self.__exclude_related = exclude_related

    @property
    def zim(self) -> Archive:
        if not self.__zim:
            self.__zim = Archive(self.__zim_path)
        return self.__zim
    
    @property
    def main_node_id(self) -> typing.Optional[str]:
        main_entry = follow_zim_redirects(self.zim.main_entry)
        return main_entry.path if main_entry else None

    @property
    def full_graph(self) -> nx.Graph:
        if not self.__full_graph:
            self.__full_graph = self.__build_zim_graph(self.zim)
        return self.__full_graph
    
    @property
    def search_graph(self) -> nx.Graph:
        if self.__exclude_related:
            return self.category_articles_graph
        else:
            return self.all_articles_graph

    @property
    def categories_graph(self) -> nx.Graph:
        # The categories graph only category nodes:
        # - _CATEGORIES -> category_node
        return nx.induced_subgraph(
            self.full_graph,
            nx.descendants_at_distance(self.full_graph, CATEGORIES_NODE, 1)
        )

    @property
    def all_articles_graph(self) -> nx.Graph:
        # The articles graph contains only article nodes:
        # - _ARTICLES -> article_node
        return nx.induced_subgraph(
            self.full_graph,
            nx.descendants_at_distance(self.full_graph, ARTICLES_NODE, 1)
        )

    @property
    def category_articles_graph(self) -> nx.Graph:
        # The category articles graph contains both category nodes and nodes
        # directly adjacent to category nodes:
        # - _CATEGORIES -> category_node
        # - _CATEGORIES -> category_node -> article_node
        return nx.induced_subgraph(
            self.full_graph,
            set.union(
                nx.descendants_at_distance(self.full_graph, CATEGORIES_NODE, 1),
                nx.descendants_at_distance(self.full_graph, CATEGORIES_NODE, 2)
            )
        )
        
    def prompt_for_article_id(self) -> typing.Optional[str]:
        input_str = click.prompt("Enter an article")

        if self.search_graph.has_node(input_str):
            return input_str

        search_article_ids_list = list(
            itertools.islice(
                filter(
                    self.search_graph.has_node, self.__zim_search_iter(input_str)
                ),
                10
            )
        )

        click.echo("\nSearch results:")
        for article_id in search_article_ids_list:
            click.echo(" * {article_id}".format(
                article_id=click.style(f"{article_id}", bold=True)
            ))
        if not search_article_ids_list:
            click.secho("(No search results)", dim=True)
        click.echo()
        
        return None
    
    def format_nodes_list(self, main_node_id: str):
        main_node_categories_graph = nx.induced_subgraph(
            self.search_graph,
            set.union(
                {main_node_id},
                nx.descendants(self.search_graph, main_node_id)
            )
        )

        for node, successors_list in  nx.bfs_successors(main_node_categories_graph, main_node_id, sort_neighbors=sorted):
            if self.categories_graph.has_node(node):
                breadcrumbs = nx.shortest_path(self.categories_graph, main_node_id, node)
                yield "{heading}:\n".format(
                    heading=self.__format_category_breadcrumbs(breadcrumbs)
                )
                for successor in successors_list:
                    yield f" * {self.__format_article_link(successor)}\n"
                yield "\n"

        uncategorized_nodes_list = sorted(
            set.difference(
                set(self.search_graph.nodes),
                set(main_node_categories_graph.nodes)
            )
        )

        yield "Uncategorized articles:\n"
        for node in uncategorized_nodes_list:
            yield f" * {self.__format_article_link(node)}\n"
        if not uncategorized_nodes_list:
            yield click.style("(No uncategorized articles)\n", dim=True)

    def format_article_details(self, article_id: str) -> typing.Iterable[str]:
        article_data = self.search_graph.nodes.get(article_id)

        article_title = article_data.get("title")
        article_mimetype = article_data.get("mimetype")

        yield click.style(f"{article_title}\n", bold=True)
        yield click.style(f"{article_mimetype}\n", dim=True)
        yield "\n"

        forward_links_list = sorted(
            self.__format_article_link(node_id)
            for node_id in self.search_graph.successors(article_id)
            if node_id not in [*SPECIAL_NODES, article_id]
        )

        backward_links_list = sorted(
            self.__format_article_link(node_id)
            for node_id in self.search_graph.predecessors(article_id)
            if node_id not in [*SPECIAL_NODES, article_id]
        )

        yield "Forward links:\n"
        for link_str in forward_links_list:
            yield f" * {link_str}\n"
        if not forward_links_list:
            yield click.style("(No forward links)\n", dim=True)

        yield "\n"

        yield "Backward links:\n"
        for link_str in backward_links_list:
            yield f" * {link_str}\n"
        if not backward_links_list:
            yield click.style("(No backward links)\n", dim=True)

    def __format_category_breadcrumbs(self, breadcrumbs: list[str]) -> str:
        separator = click.style(" / ", dim=True)
        return separator.join(
            [*breadcrumbs[:-1], self.__format_article_link(breadcrumbs[-1])]
        )

    def __format_article_link(self, node_id: str) -> str:
        node_data = self.full_graph.nodes.get(node_id)
        node_title = node_data.get("title")
        if node_title:
            return "{id} {title}".format(
                id=click.style(f"{node_id}", bold=True),
                title=click.style(f"({node_title})", dim=True)
            )
        else:
            return "{id}".format(
                id=click.style(f"{node_id}", bold=True)
            )

    def __zim_search_iter(self, query: str) -> typing.Iterable[str]:
        searcher = Searcher(self.zim)
        query = Query().set_query(query)
        search = searcher.search(query)

        search_start = 0
        search_count = 50

        while True:
            search_results = list(search.getResults(0, 10))

            if search_results:
                yield from search_results
                search_start += search_count
            else:
                return
    
    def __build_zim_graph(self, zim: Archive) -> nx.Graph:
        all_entry_ids = range(zim.all_entry_count)
        all_entry_id_groups = list(grouper(all_entry_ids, ALL_ARTICLES_GRAPH_CHUNK_SIZE))
        graphs_count = len(all_entry_id_groups)

        with Pool() as pool:
            graphs = pool.imap_unordered(
                partial(zim_entries_to_graph, zim.filename.as_posix()),
                all_entry_id_groups
            )
            with click.progressbar(label="Reading articles", iterable=graphs, length=graphs_count, file=sys.stderr) as graphs_progress:
                return nx.compose_all(graphs_progress)


@click.group()
@click.argument("zim_path", type=click.Path(readable=True))
@click.option("-x", "--exclude-related", is_flag=True, help="Only show articles which are listed in category pages")
@click.pass_context
def main(ctx, zim_path, exclude_related=False):
    ctx.ensure_object(dict)
    ctx.obj['MAIN'] = ZimExplorerContext(zim_path, exclude_related)


@main.command(name="explore")
@click.pass_context
def explore_command(ctx):
    main_context = ctx.obj['MAIN']

    while True:
        click.echo("Graph contains {count} articles".format(
            count=click.style(f"{main_context.all_articles_graph.order()}", bold=True)
        ))
        click.echo("{count} articles are directly related to categories".format(
            count=click.style(f"{main_context.category_articles_graph.order()}", bold=True)
        ))

        article_id = main_context.prompt_for_article_id()

        if article_id:
            click.echo_via_pager(
                main_context.format_article_details(article_id)
            )


@main.command(name="list")
@click.pass_context
def list_command(ctx):
    main_context = ctx.obj['MAIN']

    click.echo_via_pager(
        main_context.format_nodes_list(main_context.main_node_id)
    )


def zim_entries_to_graph(zim_path: str, entry_ids: typing.Iterable[int]) -> nx.Graph:
    zim = Archive(zim_path)

    graph = nx.DiGraph()
    graph.add_node(ARTICLES_NODE)
    graph.add_node(CATEGORIES_NODE)

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

    if entry.path.startswith("Category:"):
        graph.add_edge(CATEGORIES_NODE, entry.path)

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


def follow_zim_redirects(zim_entry: Entry) -> Entry:
    while zim_entry.is_redirect:
        zim_entry = zim_entry.get_redirect_entry()
    return zim_entry


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
    main(obj={})
