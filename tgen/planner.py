#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Sentence planning: Generating T-trees from dialogue acts.
"""

from __future__ import unicode_literals
from collections import deque
from UserDict import DictMixin

from logf import log_debug
from tree import TreeData, TreeNode, NodeData
from alex.components.nlg.tectotpl.core.util import first


class CandidateList(DictMixin):
    """List of candidate trees that can be quickly checked for membership and
    can yield the best-scoring candidate quickly.

    The implementation involves a dictionary and a heap."""

    def __init__(self, members=None):
        self.queue = []
        self.members = {}
        if members:
            self.pushall(members)
        pass

    def __nonzero__(self):
        return len(self.members) > 0

    def __contains__(self, key):
        return key in self.members

    def __getitem__(self, key):
        return self.members[key]

    def __setitem__(self, key, value):
        # slow if key is in list
        if key in self:
            if value == self[key]:
                return
            queue_index = (i for i, v in enumerate(self.queue) if v[1] == key).next()
            self.queue[queue_index] = (value, key)
            self._siftup(queue_index)
        else:
            self.queue.append((value, key))
            self._siftdown(0, len(self.queue) - 1)
        self.members[key] = value

    def __delitem__(self, key):
        del self.members[key]  # this will raise an exception if the key is not there
        queue_index = (i for i, v in enumerate(self.queue) if v[1] == key).next()
        self.queue[queue_index] = self.queue[-1]
        del self.queue[-1]
        self._siftup(queue_index)

    def keys(self):
        return self.members.keys()

    def pop(self):
        """Return the first item on the heap and remove it."""
        last = self.queue.pop()  # raises appropriate IndexError if heap is empty
        if self.queue:
            value, key = self.queue[0]
            self.queue[0] = last
            self._siftup(0)
        else:
            value, key = last
        del self.members[key]
        return key, value

    def peek(self):
        """Return the first item on the heap, but do not remove it."""
        value, key = self.queue[0]
        return key, value

    def push(self, key, value):
        """Push one key-value pair to the heap."""
        self[key] = value  # calling __setitem__; it will test for membership

    def pushall(self, members):
        """Push all members of the given structure to the heap
        (a list of pairs key-value or a dictionary are accepted)."""
        if isinstance(members, dict):
            members = members.iteritems()
        for key, value in members:
            self[key] = value

    def prune(self, size):
        """Trim the list to the given size, return the rest."""
        if len(self.queue) <= size:  # don't do anything if we're small enough already
            return {}
        pruned_queue = []
        pruned_members = {}
        for _ in xrange(size):
            key, val = self.pop()
            pruned_queue.append((val, key))
            pruned_members[key] = val
        remain_members = self.members
        self.members = pruned_members
        self.queue = pruned_queue
        return remain_members

    def __repr__(self):
        return ' '.join(['%6.3f' % val for val, _ in self.queue])

    def _siftdown(self, startpos, pos):
        """Copied from heapq._siftdown, with custom comparison (comparing *just* by 1st element)"""
        heap = self.queue
        newitem = heap[pos]
        # Follow the path to the root, moving parents down until finding a place
        # newitem fits.
        while pos > startpos:
            parentpos = (pos - 1) >> 1
            parent = heap[parentpos]
            if newitem[0] < parent[0]:
                heap[pos] = parent
                pos = parentpos
                continue
            break
        heap[pos] = newitem

    def _siftup(self, pos):
        """Copied from heapq._siftup, with custom comparison (comparing *just* by 1st element)"""
        heap = self.queue
        endpos = len(heap)
        startpos = pos
        newitem = heap[pos]
        # Bubble up the smaller child until hitting a leaf.
        childpos = 2 * pos + 1  # leftmost child position
        while childpos < endpos:
            # Set childpos to index of smaller child.
            rightpos = childpos + 1
            if rightpos < endpos and not heap[childpos] < heap[rightpos]:
                childpos = rightpos
            # Move the smaller child up.
            heap[pos] = heap[childpos]
            pos = childpos
            childpos = 2 * pos + 1
        # The leaf at pos is empty now.  Put newitem there, and bubble it up
        # to its final resting place (by sifting its parents down).
        heap[pos] = newitem
        self._siftdown(startpos, pos)


class SentencePlanner(object):
    """Common ancestor of sentence planners."""

    def __init__(self, cfg):
        """Initialize, setting language, selector, and successor generator"""
        self.language = cfg.get('language', 'en')
        self.selector = cfg.get('selector', '')
        # candidate generator
        self.candgen = cfg['candgen']

    def generate_tree(self, da, gen_doc=None):
        """Generate a tree given input DA.

        @param gen_doc: if this is None, return the tree as a TreeData object, otherwise append \
            to a t-tree document
        """
        raise NotImplementedError

    def get_target_zone(self, gen_doc):
        """Find the first bundle in the given document that does not have the target
        zone (or create it), then create the target zone and return it.

        @rtype: Zone
        """
        bundle = first(lambda bundle: not bundle.has_zone(self.language, self.selector),
                       gen_doc.bundles) or gen_doc.create_bundle()
        zone = bundle.create_zone(self.language, self.selector)
        return zone


class SamplingPlanner(SentencePlanner):
    """Random t-tree generator given DAs.

    Trainable from DA distributions
    """

    MAX_TREE_SIZE = 50

    def __init__(self, cfg):
        super(SamplingPlanner, self).__init__(cfg)
        # ranker (selecting the best candidate)
        self.ranker = None
        if 'ranker' in cfg:
            self.ranker = cfg['ranker']

    def generate_tree(self, da, gen_doc=None):
        root = TreeNode(TreeData())
        cdfs = self.candgen.get_merged_cdfs(da)
        nodes = deque([self.generate_child(root, da, cdfs[root.formeme])])
        treesize = 1
        while nodes and treesize < self.MAX_TREE_SIZE:
            node = nodes.popleft()
            if node.formeme not in cdfs:  # skip weirdness
                continue
            for _ in xrange(self.candgen.get_number_of_children(node.formeme)):
                child = self.generate_child(node, da, cdfs[node.formeme])
                nodes.append(child)
                treesize += 1
        if gen_doc:
            zone = self.get_target_zone(gen_doc)
            zone.ttree = root.create_ttree()
            return
        return root.tree

    def generate_child(self, parent, da, cdf):
        """Generate one t-node, given its parent and the CDF for the possible children."""
        if self.ranker:
            formeme, t_lemma, right = self.ranker.get_best_child(parent, da, cdf)
        else:
            formeme, t_lemma, right = self.candgen.sample(cdf)
        child = parent.create_child(right, NodeData(t_lemma, formeme))
        return child


class ASearchPlanner(SentencePlanner):
    """Sentence planner using A*-search."""

    MAX_ITER = 10000

    def __init__(self, cfg):
        super(ASearchPlanner, self).__init__(cfg)
        self.ranker = cfg['ranker']
        self.max_iter = cfg.get('max_iter', self.MAX_ITER)
        self.max_defic_iter = cfg.get('max_defic_iter')

    def generate_tree(self, da, gen_doc=None, return_lists=False):
        log_debug('GEN TREE for DA: %s' % unicode(da))
        # generate and use only 1-best
        open_list, close_list = self.run(da, self.max_iter, self.max_defic_iter)
        best_tree = close_list.peek()[0]
        log_debug("RESULT: %s" % unicode(best_tree))
        # return or append the result, return open & close list for inspection if needed
        if gen_doc:
            zone = self.get_target_zone(gen_doc)
            zone.ttree = best_tree.create_ttree()
            zone.sentence = unicode(da)
        if return_lists:
            return open_list, close_list
        if gen_doc:
            return
        return best_tree

    def run(self, da, max_iter=None, max_defic_iter=None, beam_size=None):
        """Run the A*-search generation and after it finishes, return the open
        and close lists.

        @param da: the input dialogue act
        @param max_iter: maximum number of iterations for generation
        @param gold_ttree: a gold t-tree to check if it matches the current candidate
        @rtype: tuple
        @return: the resulting open and close lists
        """
        # TODO add future cost ?

        # initialization
        empty_tree = TreeData()
        open_list = CandidateList({empty_tree: self.ranker.score(empty_tree, da) * -1})
        close_list = CandidateList()
        num_iter = 0
        defic_iter = 0
        cdfs = self.candgen.get_merged_cdfs(da)
        if not max_iter:
            max_iter = self.max_iter

        # main search loop
        while open_list and num_iter < max_iter and (max_defic_iter is None
                                                     or defic_iter <= max_defic_iter):
            # log_debug("   OPEN : %s" % str(open_list))
            # log_debug("   CLOSE: %s" % str(close_list))
            cand, score = open_list.pop()
            close_list.push(cand, score)
            log_debug("--- IT %05d: [O: %5d C: %5d]" % (num_iter, len(open_list), len(close_list)))
            log_debug("              [S:   %8.4f    ] %s" % (score, unicode(cand)))
            successors = self.candgen.get_all_successors(cand, cdfs)
            # add candidates with score
            open_list.pushall([(s, self.ranker.score(s, da) * -1)
                               for s in successors if not s in close_list])
            # pruning (if supposed to do it)
            # TODO do not even add them on the open list when pruning
            if beam_size is not None:
                pruned = open_list.prune(beam_size)
                close_list.pushall(pruned)
            num_iter += 1
            # check where the score is higher -- on the open or on the close list
            # keep track of 'deficit' iterations (and do not allow more than the threshold)
            if open_list and close_list:
                open_best_score, close_best_score = open_list.peek()[1], close_list.peek()[1]
                if open_best_score <= close_best_score:  # scores are negative, less is better
                    defic_iter = 0
                else:
                    defic_iter += 1

            if num_iter == max_iter:
                log_debug('ITERATION LIMIT REACHED')
            elif defic_iter == max_defic_iter:
                log_debug('DEFICIT ITERATION LIMIT REACHED')

        return open_list, close_list
