// The helpers of pypl4g's check.py that need no checker: its module-level
// functions, defined in check-helpers.cc.
#pragma once

#include "sema/check-types.hh"


namespace pl4g::sema {

  // -- helpers ----------------------------------------------------------------

  // Which branches reach each block of a function.
  using Incoming = std::unordered_map<const ir::BasicBlock*, std::vector<const ir::BlockTarget*>>;

  // Each is a function of check.py's module level, by the name it has there
  // without the underscore -- but for five a method of the checker shares a
  // name with: `_made_here` is `made_where_written`, `_one_of` `alternatives`,
  // `_shape_written` `shape_of_literal`, `_written_in` `collect_written` and
  // `_counted` `counted_bits`.  A helper making nodes takes the arena to
  // make them in.

  // Where a command of a documentation comment is, in the file.
  Span doc_span(std::span<const Span> lines, doc::Part part);  // check.py:355
  // The name a module goes by when reached through `prefix`.
  std::string found_name(std::string_view prefix, std::string_view base);  // check.py:372
  // Every name assigned anywhere in `block`, in the order they appear.
  std::vector<std::string> assigned_in(const ast::Block* block);  // check.py:377
  // Add to `into` every name `block` assigns, looking through nested bodies.
  void collect_assigned(const ast::Block* block, std::vector<std::string>& into);  // check.py:391
  // Add to `into` every name `&` is written in front of, anywhere below `node`.
  void addressed_in(const ast::Node* node, NameSet& into);  // check.py:412
  // Add to `into` every name read through with `⌖` below `node`: `r⌖`, and the target of `r⌖ ← v`. Such
  // a read is a use of the place the reference names, not a copy of the reference -- which every other
  // read of a `&mut` is, and is lent for.
  void read_through(const ast::Node* node, KeySet& into);  // check.py:475
  // The type object a definition's parts will be filled into.
  const ir::Type* shell_for(NamedType* defined);  // check.py:492
  // Put the parts that were resolved into the object that was handed out.
  const ir::Type* filled_in(const ir::Type* shell, const ir::Type* made);  // check.py:512
  // What a value of `ty` counts, which is nothing for everything but a number.
  const ir::Unit* unit_of(const ir::Type* ty);  // check.py:569
  // `ty` with `unit` on it, where a unit is a thing it can carry.
  const ir::Type* carrying(const ir::Type* ty, const ir::Unit* unit);  // check.py:574
  // The names of the fields of `node` a walk descends into.
  std::vector<std::string> children(const ast::Node* node);  // check.py:601
  // Walk `node` for what may take a lambda further than a call.
  void lambdas_handed_on(const ast::Node* node, const std::function<bool(const ast::Call*)>& calls, const NameSet& taken, std::vector<const ast::Lambda*>& handed, std::vector<std::pair<std::string, const ast::Lambda*>>& bound);  // check.py:610
  // Every name written below `node`, in the order they are written.
  void named_in(const ast::Node* node, std::vector<std::string>& into);  // check.py:658
  // Every name bound below `node`, whichever construct binds it.
  void bound_in(const ast::Node* node, NameSet& into);  // check.py:684
  // Whether a name written as a type stands for one a call settles.
  bool is_generic(std::string_view name);  // check.py:708
  // Every type parameter named below `written`, in the order they are written.
  void parameters_in(const ast::Node* written, std::vector<std::string>& into);  // check.py:713
  // Every type parameter standing where a value would, in the order written.
  void type_names_in(const ast::Node* written, std::vector<std::string>& into);  // check.py:726
  // Every hole below `node`, in the order they are written.
  void holes_of(const ast::Node* node, std::vector<const ast::Hole*>& into);  // check.py:908
  // The whole of a definition's name, which is a path where it has one.
  std::string written_name(const ast::FuncDef* node);  // check.py:937
  // The name a definition is written down under, for reporting a second one.
  std::string declared_as(const ast::FuncDef* node);  // check.py:949
  // Whether a value of `ty` holds a piece of the program anywhere inside it.
  bool holds_a_piece(const ir::Type* ty, const KeySet& seen = {});  // check.py:963
  // Whether a type is one a program defined rather than one the language has.
  bool of_its_own(const ir::Type* ty);  // check.py:986
  // Whether a reference may name a place holding a value of this type.
  bool can_be_referred_to(const ir::Type* ty);  // check.py:1004
  // Whether a value of `ty` holds a function anywhere inside it.
  bool holds_a_lambda(const ir::Type* ty, const KeySet& seen = {});  // check.py:1022
  // Whether a value of `ty` reaches nothing that is gone before the program is, whatever it was made
  // from: it points nowhere, or what it points at is in what its type says lasts.
  bool kept_anywhere(const ir::Type* ty);  // check.py:1043
  // What closes a value of `ty`, where `@[closed_by(f)]` said; nothing else.
  std::string closer_of(const ir::Type* ty);  // check.py:1063
  // The type marked `@[closed_by(f)]` a value of `ty` holds, itself or inside it; nothing where it holds
  // none.
  const ir::ProductType* closable_in(const ir::Type* ty, const KeySet& seen = {});  // check.py:1068
  // Whether `expr` makes the record it is, written out where it stands: `std.Reader(.fd ← 0i32)`, which
  // is how the standard streams are made.
  bool made_where_written(const ast::Expr* expr, const ir::Type* ty);  // check.py:1096
  // Whether `expr` is `⌜T'⌝`: a type parameter lifted, which is a type whatever it turns out to be, and
  // is asked about before it is anything.
  bool lifts_a_parameter(const ast::Expr* expr);  // check.py:1107
  // The fields a record's definition wrote `mut NAME : TYPE`.
  NameSet mutable_fields(const ast::Node* node);  // check.py:1115
  // Whether a value of `ty` holds a field written `mut` anywhere inside it -- which a variable at the
  // top level holding one has to be written where it may be changed for.
  bool has_mutable_field(const ir::Type* ty, const KeySet& seen = {});  // check.py:1122
  // A field written as the path to it, `init.io.output`, for a message.
  std::string path_of(const ast::Expr* expr);  // check.py:1140
  // Whether a loop over `iterable` takes one turn and no more, which a range from a number written down
  // to the one after it does -- the loop a generator written into its walk is wrapped in.
  bool one_turn(const ast::Expr* iterable);  // check.py:1149
  // The one field of a record, or member of a tuple, that has only one; nothing for anything else.
  const ir::Type* only_member(const ir::Type* ty);  // check.py:1159
  // Whether what `expr` is written as is, by the look of it, in the image or the heap: text written
  // down, its bytes, or something `in ⎕static` or `in ⎕heap`. Asked of what a walk kept in `⎕heap` is
  // handed where its type alone does not say it lasts.
  bool plainly_lasting(const ast::Expr* expr);  // check.py:1170
  // The walk inside a value of `ty` that is described in the room of the call that made it -- an
  // iterator, or a walk under way not kept in `⎕heap` -- and so may not outlive that call; nothing where
  // there is none.
  const ir::Type* short_lived(const ir::Type* ty);  // check.py:1185
  // Whether every function a value of `ty` holds says what it brought in lasts (`fn(…) in ⎕heap → T`):
  // such a value reaches nothing that is gone before the program is, whatever it was made from, its type
  // having been checked where it was made.
  bool lambdas_last(const ir::Type* ty, const KeySet& seen = {});  // check.py:1209
  // Every lifetime name written anywhere in a type, however deep.
  NameSet lifetimes_of(const ast::Node* node, NameSet* found = nullptr);  // check.py:1234
  // Whether `found` promises at least what `expected` asks.
  bool promises_as_much(const ir::Type* expected, const ir::Type* found, std::optional<std::string> free = {});  // check.py:1257
  // Whether every reference a function of type `found` answers with lives at least as long as `expected`
  // says: as long as what fewer of the parameters named -- or fewer parts of them -- is longer, never
  // shorter.
  bool borrows_as_much(const ir::FuncType* expected, const ir::FuncType* found, std::optional<std::string> free = {});  // check.py:1307
  // Whether `expr` is the kind of thing that names a place at all.
  bool names_a_place(const ast::Expr* expr);  // check.py:1334
  // What one step of a chain is applied to, or nothing where it is no step.
  const ast::Expr* step_inner(const ast::Expr* node);  // check.py:1353
  // The same step, applied to something else.
  const ast::Expr* step_onto(const ast::Expr* node, const ast::Expr* inner);  // check.py:1364
  // Take a value written with `¨` apart, or nothing where it is not one.
  std::optional<std::tuple<const ast::Expr*, std::int64_t, std::function<const ast::Expr*(const ast::Expr*)>>> marked(const ast::Expr* expr);  // check.py:1371
  // Whether a mask written as `expr` may be worked out a place at a time.
  bool walks_per_place(const ast::Expr* expr);  // check.py:1401
  // `value`'s low bits, read as a number of `ty`: two's complement.
  __int128 within(__int128 value, const ir::IntType* ty);  // check.py:1418
  // Whether a `kind` stands anywhere below `node`, a lambda's body apart.
  bool contains(const ast::Node* node, ast::NodeKind kind);  // check.py:1426
  // Whether `place` is the null pointer, which a walk hands a turn to say there is nowhere to write it:
  // finish.
  ir::Value* is_nowhere(ir::Builder& builder, ir::Value* place, Span span);  // check.py:1440
  // Whether a `break` of the loop `label` stands below `node`.
  bool breaks(const ast::Node* node, std::string_view label);  // check.py:1448
  // How many of `kind` stand below `node`.
  std::int64_t count(const ast::Node* node, ast::NodeKind kind);  // check.py:1460
  // Every name written below `node` that could stand for a function: a bare name, the last part of a
  // path, an operator's glyph. More than are called, which is what makes it safe to lower no more than
  // these.
  void mentioned(const ast::Node* node, NameSet& into);
  void mentioned(std::span<const ast::SourceUnit* const> units, NameSet& into);  // check.py:1471
  // The names called anywhere below `node`.
  std::vector<std::string> called_in(const ast::Node* node);  // check.py:1496
  // Every name read below `node`.
  NameSet names_in(const ast::Node* node);  // check.py:1514
  // Every name a function binds: its parameters, and every definition, loop name, `match` binding and
  // lambda parameter in its body.
  NameSet names_bound_in(const ast::FuncDef* func);  // check.py:1532
  // Every loop name below `node`.
  NameSet labels_in(const ast::Node* node);  // check.py:1560
  // `node` with `change` asked of every node below it, bottom first: what it answers stands in place of
  // the node, nothing leaving it as it is. A lambda's body is another function's, and is left alone
  // where `into_lambdas` says so.
  const ast::Node* rebuilt(ast::SyntaxArena& arena, const ast::Node* node, const std::function<const ast::Node*(const ast::Node*)>& change, bool into_lambdas = true);  // check.py:1578
  // `written` with the type parameters in `types` renamed.
  const ast::TypeExpr* types_renamed(ast::SyntaxArena& arena, const ast::TypeExpr* written, const Dict<std::string, std::string>& types);  // check.py:1599
  // `body` with the names, loop names and type parameters in `names`, `labels` and `types` renamed.
  const ast::Block* renamed(ast::SyntaxArena& arena, const ast::Block* body, const Dict<std::string, std::string>& names, const Dict<std::string, std::string>& labels, const Dict<std::string, std::string>* types = nullptr);  // check.py:1613
  // `body` with every `continue` of the loop `outer` made one of `inner`; the same block where there is
  // none.
  const ast::Block* continued(ast::SyntaxArena& arena, const ast::Block* body, std::string_view outer, std::string_view inner);  // check.py:1647
  // A generator's body with each `yield v` as `handed` makes it, each `yield ⇉ g` as a loop over `g` of
  // such, and each `return` as the walk running out: going on past the loop of one turn named `outer`.
  const ast::Block* yields_replaced(ast::SyntaxArena& arena, const ast::Block* body, const std::function<const ast::Node*(const ast::Yield*)>& handed, std::string_view outer, const std::function<std::string()>& walk_name);  // check.py:1661
  // Whether `expr` can be written out again for each element as it is.
  bool stands_alone(const ast::Expr* expr);  // check.py:1690
  // Whether writing `expr` again names the same place, at no cost.
  bool a_place_again(const ast::Expr* expr);  // check.py:1702
  // Whether a value of `ty` is a collection, and so is built and not written.
  bool is_a_table(const ir::Type* ty);  // check.py:1723
  // Whether `ty` is a collection nothing may put anything in.
  bool read_only_table(const ir::Type* ty);  // check.py:1728
  // Whether a value of `ty` may reach a place some name stands for.
  bool reaches_a_place(const ir::Type* ty);  // check.py:1733
  // Several names, written the way a sentence writes them.
  std::string and_then(std::span<const std::string> names);  // check.py:1744
  // Every name a body assigns, and every name a part of which it writes.
  void collect_written(const ast::Node* node, NameSet& into, NameSet& parts);  // check.py:1757
  // Whether a value of `ty` is a list or has one among its parts -- which is what may be changed in
  // place, and so what two names must not share.
  bool holds_a_list(const ir::Type* ty);  // check.py:1780
  // Every name `node` may change, read off what is written: given a new value, a part of it written,
  // joined onto, taken out of or made room in, walked by a cursor, lent `&mut` -- or lent to a method,
  // which may take it so -- taken room from or emptied, or walked by reference that may write. A lambda
  // that brought a name in by reference changes it by writing it in its body, which is part of what is
  // walked.
  NameSet changed_names(const ast::Node* node);  // check.py:1793
  // Every name `node` uses as the whole of what it holds rather than to read from it: handed to a call,
  // bound to another name, answered, put somewhere.
  NameSet used_whole(const ast::Node* node);  // check.py:1859
  // Each lifetime name a signature carries, with the parameters carrying it: those whose own references
  // carry it, and not a function's whose answer is said to live as long as it -- that one is what the
  // others are for.
  std::vector<std::tuple<std::string, std::vector<std::int64_t>>> carried_lifetimes(const ast::FuncDef* node, std::span<const ir::Type* const> types);  // check.py:1894
  // `node` with `fn(…) in a → T` read as what it says in a signature.
  const ast::FuncDef* kept_in_the_signature(const ast::FuncDef* node);  // check.py:1908
  // Whether `node` changes a list `name` holds -- or one inside it -- where it is: joins onto it, writes
  // an element of it, takes one out or makes room in it, directly or by walking it with a cursor. Read
  // off what is written, so a name shadowed somewhere counts too, which costs a copy and never an
  // answer.
  bool changed_in_place(std::string_view name, const ast::Node* node);  // check.py:1941
  // Whether a container of `ty` copies what is put in it into its allocator.
  bool is_owning(const ir::Type* ty);  // check.py:1977
  // Whether what an expression comes to is something nothing else names.
  bool is_temporary(const ast::Expr* written);  // check.py:1982
  // The arena of the body's own a definition hands out with its answer.
  std::optional<std::string> hands_out_of(const ast::Node* node);  // check.py:2010
  // Whether an answer of `ty` can travel without its allocator.
  bool thin_able(const ir::Type* ty);  // check.py:2032
  // The functions in the order the call tree puts them: each after what it calls.
  std::vector<Collected*> callees_first(std::span<Collected* const> collected);  // check.py:2042
  // Several names, written as the alternatives they are: `'a' or 'b'`.
  std::string alternatives(std::span<const std::string> names);  // check.py:2091
  // Where `name` stands among the fields of `ty`, if it is one of them.
  std::optional<std::int64_t> field_index(const ir::ProductType* ty, std::string_view name);  // check.py:2099
  // What to call a place in a message, where the program wrote a name.
  std::string written_as(const ast::Expr* expr);  // check.py:2107
  // Whether `value` was worked out from one of `sources`.
  bool reached_from(ir::Value* value, std::span<ir::Value* const> sources, const Incoming* incoming = nullptr, KeySet* visited = nullptr, const Dict<std::int64_t, KeySet>* only = nullptr, const Dict<std::pair<std::string, std::string>, std::vector<ir::Value*>>* outer = nullptr, std::optional<std::int64_t> leaf = std::nullopt, const KeySet& stored = {});  // check.py:2123
  // Whether a parameter written `written` is a reference to an arena that may be written, which a call
  // may leave to the one it stands in.
  bool could_be_an_arena(const ast::TypeExpr* written);  // check.py:2236
  // Whether a type written as a value stands anywhere in `node`: a condition holding one is answered
  // while compiling.
  bool compares_types(const ast::Node* node);  // check.py:2243
  // Whether a parameter of `ty` is where its function takes room from: a reference to an arena, which
  // may be written.
  bool is_an_arena_handed(const ir::Type* ty);  // check.py:2255
  // Why an array of `element` is not copied by copying its bytes, if it is not: an element pointing into
  // room something else keeps would be a second holder of it, and one holding a reference a second way
  // to a place.
  std::optional<std::string> not_copied_because(const ir::Type* element);  // check.py:2261
  // The shape of `ty` as a program writes it, a length known only while running left empty.
  std::string shape_of_type(const ir::ArrayType* ty);  // check.py:2272
  // The one of `found` bound furthest in, which goes first.
  Local* deepest(std::span<Local* const> found);  // check.py:2280
  // Which branches reach each block of `func`, by the block's identity.
  Incoming incoming_of(ir::Function* func);  // check.py:2289
  // What a call was handed, as the type it calls numbers its parameters, and what each reference in its
  // answer lives as long as.
  std::tuple<std::vector<ir::Value*>, Dict<std::int64_t, ir::Borrowed>> call_borrows(const ir::Instruction* call);  // check.py:2300
  // What of the arguments `given` the name `borrowed` says is on: each argument for a parameter carrying
  // it, or -- where it is on some parts of a tuple -- those parts, taken out where the tuple was made
  // here and otherwise the tuple with which part was meant.
  std::vector<std::tuple<ir::Value*, std::optional<std::int64_t>>> borrowed_parts(std::span<ir::Value* const> given, ir::Borrowed borrowed);  // check.py:2318
  // Part `leaf` of the tuple `value`, where it was made here and the part can be named; nothing
  // otherwise.
  ir::Value* leaf_of(ir::Value* value, std::int64_t leaf);  // check.py:2338
  // Which parts of a parameter of type `ty`, written `ref`, hold a reference carrying one of `names`:
  // nothing where it is not a tuple written out, the name then being on the whole of it.
  std::optional<std::vector<std::int64_t>> leaves_named(const ast::TypeExpr* ref, const ir::Type* ty, const NameSet& names);  // check.py:2348
  // Whether a value of `ty` may point into room something else holds.
  bool points_somewhere(const ir::Type* ty, const KeySet& seen = {}, bool lambdas = true, bool references = true);  // check.py:2370
  // Every arena name given back with `⎕empty` below `node`, `defer` or not.
  void empties_named(const ast::Node* node, NameSet& into);  // check.py:2411
  // The lifetime name on the reference a parameter's reference refers to -- `⧖a` in `&mut &mut ⧖a u32`
  // -- or nothing.
  std::optional<std::string> held_lifetime(const ast::Node* ref);  // check.py:2433
  // Every `(slot, kept)` pair of a signature: the place `slot` refers to holds a reference carrying the
  // name that `kept`'s own reference carries, so the two live as long as each other.
  std::vector<std::tuple<std::int64_t, std::int64_t>> kept_pairs(const ast::FuncDef* node);  // check.py:2441
  // Whether a value of `ty` holds, anywhere inside it, a reference that does not last as long as the
  // program -- or one that does, to a place holding such a reference. A value holding none may go
  // anywhere: what it refers to is there for as long as anything can hold it.
  bool holds_a_short_reference(const ir::Type* ty, const KeySet& seen = {});  // check.py:2457
  // Whether a value of `ty` holds a reference anywhere inside it.
  bool holds_a_reference(const ir::Type* ty, const KeySet& seen = {});  // check.py:2488
  // Whether an array of `found` stands where one of `wanted` is asked for.
  bool lets_go_of(const ir::ArrayType* found, const ir::ArrayType* wanted);  // check.py:2514
  // Which element of each dimension the `at`-th of the run is.
  std::vector<std::int64_t> spread_out(std::int64_t at, std::span<const std::int64_t> shape);  // check.py:2529
  // Whether an expression says on its own what type it has.
  bool says_its_type(const ast::Expr* expr);  // check.py:2543
  // What to lower one entry of a literal with, given what the others said.
  const ir::Type* taken_from(const ast::Expr* written, const ir::Type* said);  // check.py:2559
  // Whether a value of `ty` may be a key of a set or a dictionary.
  bool can_be_a_key(const ir::Type* ty);  // check.py:2570
  // Whether a value of `ty` may be what a dictionary's key stands for.
  bool can_be_a_value(const ir::Type* ty);  // check.py:2591
  // Whether every value of `found` is a value of `into`.
  bool every_value_fits(const ir::IntType* found, const ir::IntType* into);  // check.py:2605
  // Whether `ty` is a reference to the record a program is started with.
  bool is_startup_argument(const ir::Type* ty);  // check.py:2620
  // What the startup function's parameter stands for, where this is it.
  const ir::Type* held_by_the_startup(ir::Function* func, std::int64_t index, const ast::Param* written);  // check.py:2647
  // Whether a value of `ty` holds a device anywhere inside it.
  bool reaches_a_device(const ir::Type* ty, const KeySet& seen = {});  // check.py:2674
  // The glyph an operator application is written with.
  std::string glyph_of(const ast::Expr* expr);  // check.py:2698
  // What an operator application is applied to.
  std::vector<const ast::Expr*> operands_of(const ast::Expr* expr);  // check.py:2710
  // Whether what the expression does takes room from an arena.
  bool takes_room(const ast::Expr* expr);  // check.py:2722
  // What an expression is, for a message about where it cannot take room.
  std::string about_the_shape(const ast::Expr* expr);  // check.py:2737
  // Whether a `?` is anywhere inside `node`, which would hand a failure back.
  bool leaves(const ast::Node* node);  // check.py:2749
  // Whether `ty` is a mutable reference to the record a build is described in.
  bool is_build_object(const ir::Type* ty);  // check.py:2764
  // A path of names as it was written, `m` or `m.inner`, for a message.
  std::string path_text(const ast::Expr* expr);  // check.py:2783
  // Whether a top-level definition is one an importing file may name.
  bool is_exported(Held what);  // check.py:2811
  // The one type several parts say together: nothing where none says one, and `_MIXED` where two say
  // different ones.
  Held merged(std::span<const Held> found);  // check.py:3064
  // The shape an array written down has, nothing where its rows differ.
  std::optional<std::vector<std::int64_t>> shape_of_literal(const ast::ArrayLit* expr);  // check.py:3080
  // Which parameters of a definition its body takes as its own: `mut` ones holding a list that the body
  // changes in place.
  std::vector<std::int64_t> takes_owned(const ast::FuncDef* node, std::span<const ir::Type* const> params);  // check.py:3095
  // How many times `name` is read in `node`.
  std::int64_t mentions(const ast::Node* node, std::string_view name);  // check.py:3105
  // Whether `node` holds a `yield ⇉` written where a value is wanted.
  bool takes_an_answer(const ast::Node* node);  // check.py:3119
  // Whether a parameter of `ty` is a table the function may fill: a set or a dictionary whose type says
  // it may be written.
  bool fills(const ir::Type* ty);  // check.py:3131
  // Which parameters of a definition are arrays its body may write: `mut` ones.
  std::vector<std::int64_t> writes_arrays(const ast::Node* node, std::span<const ir::Type* const> params);  // check.py:3137
  // What an application is -- its kind, which operation, and its operands -- as a requirement and a body
  // both write it.
  std::optional<std::tuple<std::string, std::string, std::vector<const ast::Expr*>>> applied(const ast::Node* expr);  // check.py:3143
  // A requirement for one operation, as a program would write it.
  std::string requirement_written(std::string_view kind, std::string_view what, std::span<const ir::Type* const> types);  // check.py:3165
  // `expr` with its operands replaced, in the order `_applied` gives them.
  const ast::Expr* with_operands(ast::SyntaxArena& arena, const ast::Expr* expr, std::span<const ast::Expr* const> operands);  // check.py:3187
  // Whether `expr` is written `… in ⎕static`.
  bool in_the_image(const ast::Expr* expr);  // check.py:3209
  // Whether `expr` is `⸨⸩` with nothing in it, of the heap: written with no `in`, or `in ⎕heap`.
  bool empty_in_the_heap(const ast::Expr* expr);  // check.py:3216
  // Whether `expr` is an array, a list, a set or a dictionary written down, whatever `in` says of it.
  bool a_container_written(const ast::Expr* expr);  // check.py:3228
  // Whether everything `expr` says is known while compiling.
  bool known_while_compiling(const ast::Expr* expr, const std::function<bool(const ast::Member*)>& enum_named);  // check.py:3236
  // Where each entry of a table goes, worked out the way the program would.
  std::tuple<std::int64_t, std::vector<std::optional<std::tuple<const ir::Value*, const ir::Value*>>>, std::int64_t> table_laid_out(std::span<const std::tuple<Held, const ir::Value*, const ir::Value*>> keys, const std::function<std::int64_t(const Held&, std::int64_t)>& hashed);  // check.py:3270
  // Where a probe for a key of one word starts, in a table laid out in the image: its hash with that
  // table's seed, as the runtime works it out.
  std::int64_t word_hash(Held bits, std::int64_t mask);  // check.py:3311
  // Where a probe for a key of text starts, in a table laid out in the image: the hash of its bytes with
  // that table's seed.
  std::int64_t text_hash(Held bits, std::int64_t mask);  // check.py:3318
  // How many bits of `value` are set, or how many zeroes stand above the highest set one, counted over
  // `ty`'s own width.
  __int128 counted_bits(ir::UnOp op, __int128 value, const ir::IntType* ty);  // check.py:3325
  // The variable the approximate comparisons read, made once per module.
  ir::GlobalVar* tolerance(ir::Module& module);  // check.py:3339
  // The arena the compiler provides, made once per module.
  ir::GlobalVar* heap(ir::Module& module);  // check.py:3361
  // Whether an operand may be lowered before what it is an operand of is.
  bool lowered_first(const ast::Node* expr);  // check.py:3426
  // Whether `ty` is, or is made of, a type parameter being checked abstractly.
  bool holds_opaque(const ir::Type* ty, const KeySet& seen = {});  // check.py:3437
  // What a container of `⎕heap`'s carries in its provenance, to be told by.
  // Whether `key` names a container rather than an allocator.
  // What a value read out of the container `key` names carries.
  // Whether `key` says a value was read out of a container.
  // Check and lower the macros, answering the module and the trees they quoted.
  std::unique_ptr<ir::Module> check_macros(std::span<const ast::SourceUnit* const> units, DiagEngine& diags, SourceManager* sources = nullptr, ModuleRegistry* registry = nullptr, std::optional<std::filesystem::path> path = std::nullopt);  // check.py:28681
  // Check `units` and lower them into `module`.
  ir::Module& check(ir::Module& module, std::span<const ast::SourceUnit* const> units, DiagEngine& diags, ModuleRegistry* registry = nullptr, SourceManager* sources = nullptr, Notes* notes = nullptr, Conditions conditions = Conditions::check, bool whole_modules = false, bool ignore_system = false);  // check.py:28702

} // namespace pl4g::sema
