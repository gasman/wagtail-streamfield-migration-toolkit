import importlib
from functools import lru_cache
from wagtail import hooks
from wagtail.blocks import StreamBlock, StructBlock, ListBlock, Block


def import_klass(path):
    module_path, klass_name = ".".join(path.split(".")[:-1]), path.split(".")[-1]
    return getattr(importlib.import_module(module_path), klass_name)


class BaseBlockDefComparer:
    """Base class for all BlockDefComparers"""

    # weights for the importance of argument similarity and name similarity
    arg_weight = None
    name_weight = None
    kwarg_weight = None

    @classmethod
    @lru_cache
    def _compare(
        cls,
        old_name,
        old_path,
        old_args,
        old_kwargs,
        new_name,
        new_path,
        new_args,
        new_kwargs,
    ):
        # This might not be a black and white decision for all cases. However for cases like where
        # old block is a StreamBlock and compared block is a CharBlock, it is fair to say that we
        # can be certain it is not a renamed block.
        # TODO consider eg. like CharBlock, TextBlock. In that case do we give this a weight and
        # return a non zero score for blocks like char and text that may have compatible content?
        if not cls.compare_types(old_path, new_path):
            return 0

        name_similarity = cls.compare_names(old_name, new_name)
        arg_similarity = cls.compare_args(old_args, new_args)
        kwarg_similarity = cls.compare_kwargs(old_kwargs, new_kwargs)

        return (
            arg_similarity * cls.arg_weight
            + name_similarity * cls.name_weight
            + kwarg_similarity * cls.kwarg_weight
        ) / (cls.arg_weight + cls.name_weight + cls.kwarg_weight)

    @classmethod
    def compare(cls, old_def, old_name, new_def, new_name):

        if not cls.compare_types_initial(old_def, new_def):
            return 0

        # TODO it might be best to add some separate tests for the hashable_deep_deconstruct method
        # itself
        old_path, old_args, old_kwargs = cls.hashable_deep_deconstruct(old_def)
        new_path, new_args, new_kwargs = cls.hashable_deep_deconstruct(new_def)

        # breakpoint()

        # - For structural blocks, args are a list of children, and kwargs contain block options
        # like label, icon etc.
        # - For other blocks, args includes any positional arguments and kwargs contains the block
        # options. For example, SnippetChooser block would have the snippet class as a positional
        # argument. Most basic blocks like CharBlock have no args it seems.

        return cls._compare(
            old_name=old_name,
            old_path=old_path,
            old_args=old_args,
            old_kwargs=old_kwargs,
            new_name=new_name,
            new_path=new_path,
            new_args=new_args,
            new_kwargs=new_kwargs,
        )

    @staticmethod
    def compare_types_initial(old_def, new_def):
        old_path, _, _ = old_def.deconstruct()
        new_path, _, _ = new_def.deconstruct()
        return old_path == new_path

    @staticmethod
    def compare_types(old_path, new_path):
        # returns a boolean
        return old_path == new_path

    @classmethod
    def compare_args(cls, old_args, new_args):
        # returns a normalized score (0 to 1)
        raise NotImplementedError

    @classmethod
    def compare_kwargs(cls, old_kwargs, new_kwargs):
        # returns a score between 0 and 1

        # TODO consider also default kwargs - see how much work this entails
        # TODO consider all changes: both additions and removals

        if len(old_kwargs) == 0:
            return 1

        kwarg_score = 0
        new_kwargs_dict = {kwarg: value for kwarg, value in new_kwargs}
        for kwarg, value in old_kwargs:
            if kwarg in new_kwargs_dict and new_kwargs_dict[kwarg] == value:
                kwarg_score += 1

        return kwarg_score / len(old_kwargs)

    @staticmethod
    def compare_names(old_name, new_name):
        # returns a normalized score (0 to 1)
        return 1 if old_name == new_name else 0

    @classmethod
    def hashable_deep_deconstruct(cls, obj):
        # TODO check if this is a good idea?
        # if we've already computed this for a block, keep that as an attribute on the block
        # and return it. Note that this would be done only for blocks.
        if hasattr(obj, 'hashable_deep_deconstructed'):
            return getattr(obj, 'hashable_deep_deconstructed')

        if isinstance(obj, list):
            return tuple(cls.hashable_deep_deconstruct(value) for value in obj)
        elif isinstance(obj, tuple):
            return tuple(cls.hashable_deep_deconstruct(value) for value in obj)
        elif isinstance(obj, dict):
            return tuple(
                (key, cls.hashable_deep_deconstruct(value))
                for key, value in obj.items()
            )
        elif isinstance(obj, type):
            return obj
        elif hasattr(obj, "deconstruct"):
            path, args, kwargs = obj.deconstruct()
            setattr(obj, 'hashable_deep_deconstructed', (
                path,
                tuple(cls.hashable_deep_deconstruct(value) for value in args),
                tuple(
                    (key, cls.hashable_deep_deconstruct(value))
                    for key, value in kwargs.items()
                ),
            ))
            return getattr(obj, 'hashable_deep_deconstructed')
        else:
            return obj


class StructuralBlockDefComparer(BaseBlockDefComparer):
    # Defines `compare_args` method for blocks containing children, i.e., list, stream and struct
    # blocks. This will in turn call 2 methods,
    # `compare_children` to compare the children recursively
    # `compare_kwargs` to compare other block options like label etc.

    @classmethod
    def compare_args(cls, old_args, new_args):
        old_children = old_args[0]
        new_children = new_args[0]
        return cls.compare_children(old_children, new_children)

    @staticmethod
    def compare_children(old_children, new_children):
        # returns a score between 0 and 1

        child_score_sum = 0
        #  TODO  deconstruct
        new_children_by_name = {
            new_child_name: new_child_tuple
            for new_child_name, new_child_tuple in new_children
        }
        for old_child_name, (
            old_child_path,
            old_child_args,
            old_child_kwargs,
        ) in old_children:
            old_child_def = import_klass(old_child_path)
            comparer: BaseBlockDefComparer = (
                block_def_comparer_registry.get_block_def_comparer(old_child_def)
            )
            # TODO do a process similar to what we do outside, where we rank blocks and pick one
            # to map to.
            # TODO problem: what if we map one block, but then there would be another block which
            # we come across afterwards that would have better mapped to it? Also if we're unsure,
            # do we simply leave blocks unmapped altogether?
            if old_child_name in new_children_by_name:
                new_child_path, new_child_args, new_child_kwargs = new_children_by_name[
                    old_child_name
                ]
                child_score_sum += comparer._compare(
                    old_name=old_child_name,
                    old_path=old_child_path,
                    old_args=old_child_args,
                    old_kwargs=old_child_kwargs,
                    new_name=old_child_name,
                    new_path=new_child_path,
                    new_args=new_child_args,
                    new_kwargs=new_child_kwargs,
                )

        return child_score_sum / len(old_children)


class DefaultBlockDefComparer(BaseBlockDefComparer):
    """Default used when no other comparer is available"""

    arg_weight = 0.1
    name_weight = 1
    kwarg_weight = 0.1

    @classmethod
    def compare_args(cls, old_args, new_args):
        # If the old def had no args, then return 1
        # TODO do we need to get a difference between the args instead? Refer `compare_kwargs` method
        if len(old_args) == 0:
            return 1

        arg_score = 0
        for arg in old_args:
            if arg in new_args:
                arg_score += 1

        return arg_score / len(old_args)


class StreamBlockDefComparer(StructuralBlockDefComparer):
    """Comparer for StreamBlocks"""

    name_weight = 1
    arg_weight = 1
    kwarg_weight = 0.1


class StructBlockDefComparer(StructuralBlockDefComparer):
    """Comparer for StructBlocks"""

    name_weight = 1
    arg_weight = 1
    kwarg_weight = 0.1


class ListBlockDefComparer(StructuralBlockDefComparer):
    """Comparer for ListBlocks"""

    name_weight = 1
    arg_weight = 1
    kwarg_weight = 0.1

    @staticmethod
    def compare_children(old_child, new_child):
        # ListBlocks have only one child, and it does not have a name in the deconstructed tuple

        old_child_path, old_child_args, old_child_kwargs = old_child
        new_child_path, new_child_args, new_child_kwargs = new_child

        old_child_def = import_klass(old_child_path)
        comparer: BaseBlockDefComparer = (
            block_def_comparer_registry.get_block_def_comparer(old_child_def)
        )
        # Because list children don't have a given name, we're passing None for now. Alternatively,
        # we could consider passing 'item' as the name since that is how it is saved in the json.
        return comparer._compare(
            old_name=None,
            old_path=old_child_path,
            old_args=old_child_args,
            old_kwargs=old_child_kwargs,
            new_name=None,
            new_path=new_child_path,
            new_args=new_child_args,
            new_kwargs=new_child_kwargs,
        )


class BlockDefComparerRegistry:
    BASE_COMPARERS_BY_BLOCK_TYPE = {
        Block: DefaultBlockDefComparer,
        ListBlock: ListBlockDefComparer,
        StreamBlock: StreamBlockDefComparer,
        StructBlock: StructBlockDefComparer,
    }

    def __init__(self):
        self._scanned_for_comparers = False
        self.comparers_by_block_type = {}

    def _scan_for_comparers(self):
        comparers = dict(self.BASE_COMPARERS_BY_BLOCK_TYPE)

        for fn in hooks.get_hooks("register_block_def_comparers"):
            comparers.update(fn())

        self.comparers_by_block_type = comparers
        self._scanned_for_comparers = True

    def get_block_def_comparer(self, block_def):
        # find the comparer class for the most specific class in the block's inheritance tree

        if not self._scanned_for_comparers:
            self._scan_for_comparers()

        klass = block_def if isinstance(block_def, type) else type(block_def)

        for block_class in klass.__mro__:
            if block_class in self.comparers_by_block_type:
                return self.comparers_by_block_type[block_class]


block_def_comparer_registry = BlockDefComparerRegistry()
