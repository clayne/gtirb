import collections
import collections.abc
import typing
from uuid import UUID

from intervaltree import IntervalTree
from sortedcontainers import SortedDict

from .block import ByteBlock, CodeBlock, DataBlock
from .node import Node
from .proto import ByteInterval_pb2, SymbolicExpression_pb2
from .symbolicexpression import SymAddrAddr, SymAddrConst, SymbolicExpression
from .util import (
    DictLike,
    SetWrapper,
    _nodes_at_interval_tree,
    _nodes_on_interval_tree,
    _offset_interval,
    _SingleParentIndexedAttribute,
    get_desired_range,
)

SymbolicExpressionElement = typing.Tuple[
    "ByteInterval", int, SymbolicExpression
]


class ByteInterval(Node):
    """A contiguous region of bytes in a binary.

    A ByteInterval defines a relative ordering for a group of
    :class:`ByteBlock`\\s, optionally at a fixed address in memory. It also
    stores the bytes associated with these blocks.

    If two blocks are in two different ByteIntervals, then it should be
    considered safe (that is, preserving of program semantics) to move one
    block relative to the other in memory. If two blocks are in the same
    ByteInterval, then it should be considered unknown if moving the two blocks
    relative to one another in memory is a safe operation.

    :ivar ~.address: The fixed address of this interval, if present. If this
        field is present, it may indicate the original address at which this
        interval was located at in memory, or it may indicate that this block's
        address is fixed and must not be changed. If this field is not present,
        it indicates that the interval is free to be moved around in memory
        while preserving program semantics.
    :ivar ~.size: The size of this interval in bytes. If this number is greater
        than ``initialized_size``, this indicates that the high addresses taken
        up by this interval consist of uninitialized bytes. This often occurs
        in BSS sections, where data is zero-initialized rather than stored as
        zeroes in the binary.
    :ivar ~.contents: The bytes stored in this interval.
    :ivar ~.blocks: A set of all :class:`ByteBlock`\\s in this interval.
    :ivar ~.symbolic_expressions: A mapping, from offset in the interval, to a
        :class:`SymbolicExpression` in the interval.
    """

    class _BlockSet(SetWrapper):
        def __init__(self, node, *args):
            # By creating the interval tree with all of the initial items
            # instead of adding each node individually, we can shave off a
            # good chunk of the overhead of the index. Unfortunately this
            # means duplicating code that's in add.
            self._node = node  # type: ByteInterval
            self._data = set(*args)

            for v in self._data:
                if v._byte_interval is not None:
                    v._byte_interval.blocks.discard(v)
                v._byte_interval = self._node
                if self._node.ir is not None:
                    v._add_to_uuid_cache(self._node.ir._local_uuid_cache)

            assert not self._node._interval_tree
            self._node._interval_tree = IntervalTree(
                _offset_interval(v) for v in self._data
            )

            # We intentionally do not call super's __init__ because we
            # explicitly are avoiding its per-item adding.

        def add(self, v):
            if v._byte_interval is not None:
                v._byte_interval.blocks.discard(v)
            v._byte_interval = self._node
            self._node._index_add(v)
            if self._node.ir is not None:
                v._add_to_uuid_cache(self._node.ir._local_uuid_cache)
            return super().add(v)

        def discard(self, v):
            if v not in self:
                return
            self._node._index_discard(v)
            v._byte_interval = None
            if self._node.ir is not None:
                v._remove_from_uuid_cache(self._node.ir._local_uuid_cache)
            return super().discard(v)

    class _SymbolicExprDict(collections.abc.MutableMapping):
        def __init__(self, interval, *args):
            self._interval = interval
            self._data = SortedDict()
            for i, v in dict(*args).items():
                self[i] = v

        # begin functions for ABC
        def __getitem__(self, i):
            return self._data[i]

        def __setitem__(self, i, v):
            v._instances.add((self._interval, i))
            self._interval._index_add(v)
            self._data[i] = v

        def __delitem__(self, i):
            v = self._data[i]
            v._instances.discard((self._interval, i))
            self._interval._index_discard(v)
            del self._data[i]

        def __iter__(self):
            return iter(self._data)

        def __len__(self):
            return len(self._data)

        # end functions for ABC
        def __str__(self):
            return str(self._data)

        def __repr__(self):
            # We can't just return the repr of self._data because it will
            # create a SortedDict and that isn't part of our public interface.
            items = (
                "{!r}: {!r}".format(key, value)
                for key, value in self._data.items()
            )
            return "{" + ", ".join(items) + "}"

    address = _SingleParentIndexedAttribute[
        typing.Optional[int], "ByteInterval", "Module"
    ]("address", lambda self: self.section)
    size = _SingleParentIndexedAttribute[int, "ByteInterval", "Module"](
        "size", lambda self: self.section
    )

    def __init__(
        self,
        *,
        address=None,  # type: typing.Optional[int]
        size=None,  # type: typing.Optional[int]
        initialized_size=None,  # type: typing.Optional[int]
        contents=b"",  # type: typing.ByteString
        blocks=(),  # type: typing.Iterable[ByteBlock]
        symbolic_expressions={},  # type: DictLike[int, SymbolicExpression]
        uuid=None,  # type: typing.Optional[UUID]
        section=None  # type: typing.Optional["Section"]
    ):
        """
        :param address: The fixed address of this interval, if present.
        :param size: The size of this interval in bytes.
        :param initialized_size: The number of initialized bytes in this
            interval.
        :param contents: The bytes stored in this interval.
        :param blocks: A set of all :class:`ByteBlock`\\s in this interval.
        :param symbolic_expressions: A mapping, from offset in the interval, to
            a :class:`SymbolicExpression` in the interval.
        :param uuid: The UUID of this ``ByteInterval``,
            or None if a new UUID needs generated via :func:`uuid.uuid4`.
            Defaults to None.
        :param section: The :class:`Section` this interval belongs to.
        """

        if size is None:
            size = len(contents)
        if initialized_size is None:
            initialized_size = len(contents)
        if initialized_size > size:
            raise ValueError("initialized_size must be <= size!")

        super().__init__(uuid=uuid)
        self._interval_tree = IntervalTree()
        self._symbols_to_exprs = collections.defaultdict(set)
        self._section = None  # type: typing.Optional["Section"]
        self.address = address  # type: typing.Optional[int]
        self.size = size  # type: int
        self.contents = bytearray(contents)  # type: bytearray
        self.initialized_size = initialized_size
        self.blocks = ByteInterval._BlockSet(
            self, blocks
        )  # type: typing.Set[ByteBlock]
        self.symbolic_expressions = ByteInterval._SymbolicExprDict(
            self, symbolic_expressions
        )  # type: typing.Dict[int, SymbolicExpression]
        self._proto_interval = (
            None
        )  # type: typing.Optional[ByteInterval_pb2.ByteInterval]

        # Use the property setter to ensure correct invariants.
        self.section = section

    def _index_add(self, v):
        if isinstance(v, ByteBlock):
            self._interval_tree.add(_offset_interval(v))
        elif isinstance(v, SymbolicExpression) and self.module:
            self.module._index_add(v)

    def _index_discard(self, v):
        if isinstance(v, ByteBlock):
            self._interval_tree.discard(_offset_interval(v))
        elif isinstance(v, SymbolicExpression) and self.module:
            self.module._index_discard(v)

    @property
    def initialized_size(self):
        # type: () -> int
        """The number of initialized bytes in this interval.

        Not all bytes in this interval may correspond to bytes physically
        stored in the underlying file format. This can occur, for example, in
        BSS sections, which are zero-initialized at loadtime, but these zeroes
        are not stored in the file itself. If this number is smaller than
        ``size``, this indicates that any bytes past this number are
        unitialized bytes with values determined at loadtime. As such, all
        bytes past this number in this interval's byte vector are truncated
        when saving to file.
        """

        return len(self.contents)

    @initialized_size.setter
    def initialized_size(self, value):
        # type: (int) -> None
        if value > len(self.contents):
            self.contents += b"\0" * (value - len(self.contents))
        elif value < len(self.contents):
            self.contents = self.contents[:value]

    @classmethod
    def _decode_protobuf(
        cls,
        proto_interval,  # type: ByteInterval_pb2.ByteInterval
        uuid,  # type: UUID
        ir,  # type: typing.Optional["IR"]
    ):
        # type: (...) -> ByteInterval

        def decode_block(proto_block):
            if proto_block.HasField("code"):
                block = CodeBlock._from_protobuf(proto_block.code, ir)
            elif proto_block.HasField("data"):
                block = DataBlock._from_protobuf(proto_block.data, ir)
            else:
                raise TypeError(
                    "Unknown type inside proto block: %s"
                    % proto_block.WhichOneof("value")
                )

            block.offset = proto_block.offset
            return block

        # we do not decode symbolic expressions yet, because symbols have
        # not yet been decoded at this point.
        result = cls(
            address=proto_interval.address
            if proto_interval.has_address
            else None,
            size=proto_interval.size,
            contents=proto_interval.contents,
            uuid=uuid,
            blocks=(decode_block(b) for b in proto_interval.blocks),
        )
        result._add_to_uuid_cache(ir._local_uuid_cache)
        # We store the interval and IR here so we can use it later, when
        # _decode_symbolic_expressions is called.
        result._proto_interval = proto_interval
        # Return the new BI.
        return result

    def _decode_symbolic_expressions(self, ir):
        """Called by modules after symbols are decoded, but before the module
        is done decoding.
        """

        def decode_symbolic_expression(proto_expr):
            if proto_expr.HasField("addr_const"):
                return SymAddrConst._from_protobuf(
                    proto_expr.addr_const, ir.get_by_uuid
                )
            elif proto_expr.HasField("addr_addr"):
                return SymAddrAddr._from_protobuf(
                    proto_expr.addr_addr, ir.get_by_uuid
                )
            else:
                raise TypeError(
                    "Unknown type inside proto sym expr: %s"
                    % proto_expr.WhichOneof("value")
                )

        self.symbolic_expressions = {}
        for i, v in self._proto_interval.symbolic_expressions.items():
            expr = decode_symbolic_expression(v)
            expr.attributes = set(
                SymbolicExpression.Attribute(f) for f in v.attribute_flags
            )
            self.symbolic_expressions[i] = expr

        del self._proto_interval

    def _to_protobuf(self):
        # type: () -> ByteInterval_pb2.ByteInterval
        proto_interval = ByteInterval_pb2.ByteInterval()

        proto_interval.uuid = self.uuid.bytes
        if self.address is None:
            proto_interval.has_address = False
        else:
            proto_interval.has_address = True
            proto_interval.address = self.address
        proto_interval.size = self.size
        proto_interval.contents = bytes(self.contents)

        # Cannot insert blocks using proto_interval.blocks.append() in a loop
        # because append() isn't supported in older versions of protobuf. Use a
        # comprehension and extend() instead.

        def to_proto_block(block):
            proto_block = ByteInterval_pb2.Block()
            proto_block.offset = block.offset
            if isinstance(block, CodeBlock):
                proto_block.code.CopyFrom(block._to_protobuf())
            elif isinstance(block, DataBlock):
                proto_block.data.CopyFrom(block._to_protobuf())
            else:
                raise TypeError(
                    "Unknown block type in interval: %s" % type(block)
                )
            return proto_block

        proto_interval.blocks.extend(to_proto_block(b) for b in self.blocks)

        for k, v in self.symbolic_expressions.items():
            sym_exp = SymbolicExpression_pb2.SymbolicExpression()
            if isinstance(v, SymAddrConst):
                sym_exp.addr_const.CopyFrom(v._to_protobuf())
            elif isinstance(v, SymAddrAddr):
                sym_exp.addr_addr.CopyFrom(v._to_protobuf())
            else:
                raise ValueError(
                    "Expected sym expr type in interval: %s" % type(v)
                )
            sym_exp.attribute_flags.extend(a.value for a in v.attributes)
            proto_interval.symbolic_expressions[k].CopyFrom(sym_exp)

        return proto_interval

    @property
    def section(self):
        # type: () -> typing.Optional["Section"]
        """The :class:`Section` this interval belongs to."""

        return self._section

    @section.setter
    def section(self, value):
        # type: (typing.Optional["Section"]) -> None
        if self._section is not None:
            self._section.byte_intervals.discard(self)
        if value is not None:
            value.byte_intervals.add(self)

    def deep_eq(self, other):
        # type: (typing.Any) -> bool
        # Do not move __eq__. See docstring for Node.deep_eq for more info.
        if not isinstance(other, ByteInterval):
            return False
        return (
            self.uuid == other.uuid
            and self.address == other.address
            and self.contents == other.contents
            and self.size == other.size
            and len(self.blocks) == len(other.blocks)
            and all(
                self_node.deep_eq(other_node)
                for self_node, other_node in zip(
                    sorted(self.blocks, key=lambda b: b.uuid),
                    sorted(other.blocks, key=lambda b: b.uuid),
                )
            )
            and len(self.symbolic_expressions)
            == len(other.symbolic_expressions)
            and all(
                self_kv[0] == other_kv[0] and self_kv[1].deep_eq(other_kv[1])
                for self_kv, other_kv in zip(
                    sorted(
                        self.symbolic_expressions.items(), key=lambda kv: kv[0]
                    ),
                    sorted(
                        other.symbolic_expressions.items(),
                        key=lambda kv: kv[0],
                    ),
                )
            )
        )

    def __repr__(self):
        # type: () -> str

        return (
            "ByteInterval("
            "uuid={uuid!r}, "
            "address={address}, "
            "size={size}, "
            "contents={contents!r}, "
            "blocks={blocks!r}, "
            "symbolic_expressions={symbolic_expressions!r}, "
            ")".format(
                uuid=self.uuid,
                address=self.address,
                size=self.size,
                contents=self.contents,
                blocks=self.blocks,
                symbolic_expressions=self.symbolic_expressions,
            )
        )

    def byte_blocks_on(self, addrs):
        # type: (typing.Union[int, range]) -> typing.Iterable[ByteBlock]
        """Finds all the byte blocks that overlap an address or range of
        addresses.

        :param addrs: Either a ``range`` object or a single address.
        """

        if self.address is None:
            return ()

        return _nodes_on_interval_tree(
            self._interval_tree, addrs, -self.address
        )

    def byte_blocks_at(self, addrs):
        # type: (typing.Union[int, range]) -> typing.Iterable[ByteBlock]
        """Finds all the byte blocks that begin at an address or range of
        addresses.

        :param addrs: Either a ``range`` object or a single address.
        """

        if self.address is None:
            return ()

        return _nodes_at_interval_tree(
            self._interval_tree, addrs, -self.address
        )

    def code_blocks_on(self, addrs):
        # type: (typing.Union[int, range]) -> typing.Iterable[CodeBlock]
        """Finds all the code blocks that overlap an address or range of
        addresses.

        :param addrs: Either a ``range`` object or a single address.
        """

        return (
            b for b in self.byte_blocks_on(addrs) if isinstance(b, CodeBlock)
        )

    def code_blocks_at(self, addrs):
        # type: (typing.Union[int, range]) -> typing.Iterable[CodeBlock]
        """Finds all the code blocks that begin at an address or range of
        addresses.

        :param addrs: Either a ``range`` object or a single address.
        """

        return (
            b for b in self.byte_blocks_at(addrs) if isinstance(b, CodeBlock)
        )

    def data_blocks_on(self, addrs):
        # type: (typing.Union[int, range]) -> typing.Iterable[DataBlock]
        """Finds all the data blocks that overlap an address or range of
        addresses.

        :param addrs: Either a ``range`` object or a single address.
        """

        return (
            b for b in self.byte_blocks_on(addrs) if isinstance(b, DataBlock)
        )

    def data_blocks_at(self, addrs):
        # type: (typing.Union[int, range]) -> typing.Iterable[DataBlock]
        """Finds all the data blocks that begin at an address or range of
        addresses.

        :param addrs: Either a ``range`` object or a single address.
        """

        return (
            b for b in self.byte_blocks_at(addrs) if isinstance(b, DataBlock)
        )

    def symbolic_expressions_at(
        self, addrs  # type: typing.Union[int, range]
    ):
        # type: (...) -> typing.Iterable[SymbolicExpressionElement]
        """Finds all the symbolic expressions that begin at an address or
        range of addresses.

        :param addrs: Either a ``range`` object or a single address.
        :returns: Yields ``(interval, offset, symexpr)`` tuples for every
            symbolic expression in the range.
        """

        if self.address is None:
            return

        addrs = get_desired_range(addrs)
        for i in self.symbolic_expressions._data.irange(
            addrs.start - self.address,
            addrs.stop - self.address,
            inclusive=(True, False),
        ):
            if self.address + i in addrs:
                yield (self, i, self.symbolic_expressions[i])

    def _add_to_uuid_cache(self, cache):
        # type: (typing.Dict[UUID, Node]) -> None
        """Update the UUID cache when this node is added."""

        cache[self.uuid] = self
        for block in self.blocks:
            block._add_to_uuid_cache(cache)

    def _remove_from_uuid_cache(self, cache):
        # type: (typing.Dict[UUID, Node]) -> None
        """Update the UUID cache when this node is removed."""

        del cache[self.uuid]
        for block in self.blocks:
            block._remove_from_uuid_cache(cache)

    @property
    def module(self):
        # type: () -> "Module"
        """Get the module this node ultimately belongs to."""
        if self.section is None:
            return None
        return self.section.module

    @property
    def ir(self):
        # type: () -> "IR"
        """Get the IR this node ultimately belongs to."""
        if self.module is None:
            return None
        return self.module.ir
