// The checker's methods ported from check.py lines 9482 to 12195, defined in
// check-arenas.cc.  Not a header of its own: included inside the class body of `Checker` (check.hh).

    // -- what an arena holds -----------------------------------------------------
    // Whether a name is bound to an arena or to a reference to one.
    bool is_arena(Local* local);  // check.py:9484
    // The arenas what an expression comes to may point into.
    KeySet arenas_in(const ast::Node* node);  // check.py:9493
    // The walk `_arenas_in` makes.
    void collect_arenas(const ast::Node* node, KeySet& into);  // check.py:9507
    // The function a call names where its signature alone says what its answer was made in: one of this
    // file's top level, named by itself and by nothing a body binds, and not defined somewhere else.
    ir::Function* read_ahead_callee(const ast::Call* expr);  // check.py:10146
    // What a call of `func` answers was made in, read off its signature as `where_made` reads it.
    void answered_in(const ast::Call* expr, ir::Function* func, KeySet& into);  // check.py:10158
    // The key for where an array a name holds has its elements, which a view of it carries: the name's own
    // storage for one this function keeps in its frame, and the parameter for one a caller handed over --
    // whose elements are the caller's, as long as the call is. Nothing for anything else.
    KeySet array_frame_of(Local* local);  // check.py:9597
    // Whether `a⟦…⟧` is a view of the array and not one element of it: a run of one dimension, or fewer
    // indices than it has dimensions, which is a row. Where what the array is cannot be said yet, a view,
    // which costs a refusal at the worst and never a view let out unseen.
    bool a_view_of(const ast::Element* node);  // check.py:9615
    // Write down where what a name now holds was made, and check it fits.
    void made_in(std::string_view name, const ast::Expr* value, Span span, bool assigning = false);  // check.py:9627
    // Report something given to `name` that was made in `arena`, or reaches what stands for one, while
    // `name` outlives it (4614, 4570).
    void outlives_arena(std::string_view name, Local* arena, Span span);  // check.py:9673
    // The allocator the elements of the container `value` comes to are kept in.
    std::optional<std::int64_t> holder_of(const ast::Expr* value);  // check.py:9689
    // Say in the report log how a name's allocator is known, which is the compiler's choice of whether the
    // value has to carry it.
    void report_allocator(Local* local, const KeySet& made, Span span);  // check.py:9714
    // Check that what a block comes to was not made in an arena it ends (4614).
    void leaves_no_arena_behind(const ast::Expr* value, std::int64_t opened_at, ir::Function* func, Span span, const ir::Type* ty = nullptr);  // check.py:9767
    // Everything made in an arena goes with it, from here on.
    void given_back(Local* arena, Span span);  // check.py:9786
    // What every name in reach was made in and whether it is dead, as it stands.
    Provenance provenance();  // check.py:9802
    // Make what `state` says of every name in it true again.
    void put_back(const Provenance& state);  // check.py:9813
    // What holds where several ways through meet: the union of what a name was made in, and dead where it
    // is dead along any of them.
    Provenance joined(const Provenance& before, std::span<const Provenance> ways);  // check.py:9820
    // What a way out may change and the way that goes on must not see.
    std::tuple<Provenance, std::vector<KeySet>> on_the_way_out();  // check.py:9844
    // Put back what `_on_the_way_out` took.
    void left(const std::tuple<Provenance, std::vector<KeySet>>& kept);  // check.py:9853
    // Names bound to a part of something were made where the whole was.
    void taken_out_of(const ast::Expr* whole, std::span<const std::string> names);  // check.py:9860
    // What a turn of a loop may begin with, which is what any turn may end with.
    Provenance a_turn_begins(const ast::Block* body);  // check.py:9876
    // What holds after a loop: what holds where it may be left.
    void after_the_turns(const Provenance& head, const std::optional<Provenance>& end, Loop* loop, bool goes_round = true, bool test_leaves = true);  // check.py:9901
    // Refuse a turn that ends with a variable from outside the loop moved, where the turn began with it
    // holding something (4685).
    void goes_round(const Provenance& head, std::optional<Span> span, const std::optional<Provenance>& end = std::nullopt);  // check.py:9924
    // What a field, an element or an entry was given, added to the whole.
    void made_in_part(const ast::Expr* base, const ast::Expr* value, Span span, bool element = false);  // check.py:9945
    // The one allocator `made` names, where it names exactly one.
    std::optional<std::int64_t> owner_of(const KeySet& made);  // check.py:9981
    // Where the allocator `owner` names keeps its state.
    ir::Value* owner_place(ir::Builder& builder, std::int64_t owner, Span span);  // check.py:9998
    // What a report calls the allocator `owner`.
    std::string owner_written(std::int64_t owner);  // check.py:10007
    // Whether what `key` names lasts as long as a container made with `holder`.
    bool lasts(std::int64_t key, std::int64_t holder, std::int64_t depth, bool local);  // check.py:10014
    // `value` as an element of a container whose elements are kept in `holder`.
    std::tuple<ir::Value*, KeySet, ir::Value*> stored(ir::Builder& builder, const ast::Expr* written, ir::Value* value, const ir::Type* ty, std::int64_t holder, std::int64_t depth, bool local, Span span);  // check.py:10030
    // Kill every name read out of a container that may have replaced elements.
    void elements_replaced(const KeySet& keys, std::string_view name, Span span);  // check.py:10090
    // What a function answers, kept in the allocator its signature names.
    ir::Value* answered(ir::Builder& builder, const ast::Expr* written, ir::Value* value, ir::Function* func, Span span);  // check.py:10107
    // Check that a lambda leaving the call reaches nothing that stays (4632).
    bool lambda_may_leave(const ast::Expr* written, const KeySet& lasting, std::string_view name, Span span);  // check.py:10222
    // What a report calls something a value reaches, by its key.
    std::string reach_written(std::int64_t key);  // check.py:10245
    // Whether `written` comes to an object the heap just made.
    bool freshly_heap(const ast::Expr* written);  // check.py:10261
    // Check that a body handing out an arena of its own made it (4627).
    void handed_out_made(ir::Function* func, const ast::Node* node);  // check.py:10282
    // Write on the function where its answer is made, where that is one place.
    void settle_answer(ir::Function* func, const ast::FuncDef* node);  // check.py:10295
    // Kill what an arena given back inside a loop held from before the loop.
    void before_a_turn(const ast::Block* body);  // check.py:10348
    // Put a statement off until the block it is written in is left.
    void lower_defer(const ast::Defer* stmt);  // check.py:10367
    // Put off giving back an arena this body made until its block is left.
    void give_back_on_leaving(const ast::VarDef* node);  // check.py:10397
    // Lower what every block from the innermost down to `down_to` put off.
    void run_deferred(ir::Builder& builder, ir::Function* func, std::int64_t down_to);  // check.py:10414
    // Lower one deferred statement, as it was meant where it was written.
    void lower_deferred(ir::Builder& builder, ir::Function* func, Deferred* entry);  // check.py:10430
    // Check, once, what a block put off and no way out of it ever ran.
    void unrun_deferred(ir::Function* func, std::int64_t frame);  // check.py:10459
    // Check what a `post` clause demands, and then return.
    void returning(ir::Builder& builder, ir::Value* value, Span span);  // check.py:10481
    // Lower one condition and emit the check that stops where it fails.
    void demanded(ir::Builder& builder, const ast::Clause* clause, ir::Function* func, std::string_view what, std::int64_t status);  // check.py:10522
    // What becomes of a condition the compiler settled.
    void already(const ast::Clause* clause, ir::Function* func, bool holds);  // check.py:10569

    // -- bodies ------------------------------------------------------------------
    // Check and lower one function body.
    void lower_function(Collected* entry);  // check.py:10591
    // Log where each of `node`'s variables' scopes ends, once however many times it is lowered: after its
    // last use, which is what a reader asking how long a variable is needed is asking.
    void report_scopes(const ast::FuncDef* node);  // check.py:10628
    // Take a function out of the module, and out of every cache of it.
    void discard(ir::Function* func);  // check.py:10639
    // Check and lower the statements of one function.
    void lower_body(Collected* entry);  // check.py:10649
    // Lower the statements of one block, and hand back what it comes to.
    ir::Value* lower_block(ir::Builder& builder, const ast::Block* block, ir::Function* func, bool as_result = true, const ir::Type* wanted = nullptr, bool produces = false);  // check.py:10814
    // Lower the last statement of an arm a value is wanted of.
    ir::Value* lower_yielding(ir::Builder& builder, const ast::Stmt* stmt, ir::Function* func, const ir::Type* wanted);  // check.py:10887
    // Lower one statement with whatever it says it raises in force.
    void lower_attributed_stmt(ir::Builder& builder, const ast::Stmt* stmt, ir::Function* func, bool is_last);  // check.py:10933
    // Lower one statement.
    void lower_stmt(ir::Builder& builder, const ast::Stmt* stmt, ir::Function* func, bool is_last);  // check.py:10959
    // Put what `name` stands for in the session's `variable`, where the
    // interactive session keeps what an input bound.
    void kept_by_the_session(ir::Builder& builder, std::string_view variable, std::string_view name, Span span,
                             ir::Function* func);

    // -- tuples ------------------------------------------------------------------
    // Lower `〈a, b〉`: several values made into one.
    ir::Value* lower_tuple(ir::Builder& builder, const ast::TupleLit* expr, const ir::Type* expected);  // check.py:11090
    // The type each name of a destructuring stands for, or nothing where the value cannot be taken apart
    // that way.
    std::optional<std::vector<const ir::Type*>> taken_apart(ir::Value* value, std::span<const std::tuple<std::string, Span>> names, Span span);  // check.py:11123
    // Where member `at` of the tuple or record at `place` is.
    ir::Value* member_place(ir::Builder& builder, ir::Value* place, const ir::Type* ty, std::int64_t at, Span span);  // check.py:11140
    // Member `at` of a tuple or a record, as the one value it is.
    ir::Value* member(ir::Builder& builder, ir::Value* value, const ir::Type* ty, std::int64_t at, Span span);  // check.py:11155
    // A tuple of `values`, one for each member.
    ir::Value* made_tuple(ir::Builder& builder, std::span<ir::Value* const> values, const ir::TupleType* ty, Span span);  // check.py:11176

    // -- sets and dictionaries ---------------------------------------------------

    // -- arrays ------------------------------------------------------------------
    // Lower `⟦a, b, c⟧`: several values of one type, written down.
    ir::Value* lower_array(ir::Builder& builder, const ast::ArrayLit* expr, const ir::Type* expected);  // check.py:11199
    // What type an array written down has, and what its elements came to.
    std::tuple<const ir::ArrayType*, std::optional<std::vector<ir::Value*>>> array_written(ir::Builder& builder, const ast::ArrayLit* expr, const ir::Type* expected);  // check.py:11237
    // How deep and how wide an array written down is, and what is innermost.
    std::tuple<std::vector<std::optional<std::uint64_t>>, std::vector<const ast::Expr*>> shape_written(const ast::ArrayLit* expr);  // check.py:11280
    // Write what was written down into the run of elements, in order.
    bool fill(ir::Builder& builder, const ast::ArrayLit* expr, const ir::ArrayType* ty, ir::Value* place, std::int64_t at, std::span<const std::optional<std::uint64_t>> shape, const std::vector<ir::Value*>* ready = nullptr);  // check.py:11302
    // Lower an index, which belongs to no operator and no assignment.
    ir::Value* on_its_own(ir::Builder& builder, const ast::Expr* written);  // check.py:11357
    // Where one field of a record is, given where the record is.
    ir::Value* field_place(ir::Builder& builder, ir::Value* where, const ir::ProductType* ty, std::int64_t at, Span span);  // check.py:11381
    // Write what `expr` comes to into the place `where`.
    void build_record(ir::Builder& builder, ir::Value* where, const ast::Expr* expr, const ir::ProductType* ty, Span span);  // check.py:11399
    // Whether `expr` writes a value of `ty` out, field by field.
    bool is_record_literal(const ast::Expr* expr, const ir::ProductType* ty);  // check.py:11431
    // What each field of `ty` was given, checking that each was given once.
    std::optional<Dict<std::string, const ast::Named*>> fields_given(const ast::Call* expr, const ir::ProductType* ty);  // check.py:11441
    // Write a record into the place `where`, a field at a time.
    std::int64_t record_into(ir::Builder& builder, ir::Value* where, ir::Value* value, const ir::ProductType* ty, Span span, std::int64_t first = 0);  // check.py:11476
    // Read a record out of the place `where`, a leaf at a time -- or, for one held in memory, its bytes
    // into room of its own.
    ir::Value* record_from(ir::Builder& builder, ir::Value* where, const ir::ProductType* ty, Span span);  // check.py:11509
    // What `value` is made of, which for a record is its leaves.
    std::vector<ir::Value*> leaves_of(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span);  // check.py:11520
    // Every leaf of the record at `where`, in the order it is made of.
    std::vector<ir::Value*> leaves_from(ir::Builder& builder, ir::Value* where, const ir::ProductType* ty, Span span);  // check.py:11532
    // Where the element at `index` is, given where the first one is.
    ir::Value* element_place(ir::Builder& builder, ir::Value* base, const ir::Type* element, ir::Value* index, Span span);  // check.py:11544
    // Where the elements of an array are, and how many along each dimension.
    std::tuple<ir::Value*, std::vector<ir::Value*>> shape_of(ir::Builder& builder, ir::Value* base, const ir::ArrayType* ty, Span span);  // check.py:11556
    // How many elements apart two neighbours along each dimension are.
    std::vector<ir::Value*> strides_of(ir::Builder& builder, ir::Value* base, const ir::ArrayType* ty, Span span);  // check.py:11571
    // The strides of elements that are one run, with `lengths` along each dimension: one along the last,
    // and each before it the run after it.
    std::vector<ir::Value*> dense_strides(ir::Builder& builder, std::span<ir::Value* const> lengths, Span span);  // check.py:11592
    // An array whose type does not say its shape: where its first element is, how many there are along
    // each dimension, and how far apart.
    ir::Value* view(ir::Builder& builder, ir::Value* place, std::span<ir::Value* const> lengths, std::span<ir::Value* const> strides, const ir::ArrayType* ty, Span span);  // check.py:11603
    // A view of elements that are one run, as a run just made is.
    ir::Value* dense_view(ir::Builder& builder, ir::Value* place, std::span<ir::Value* const> lengths, const ir::ArrayType* ty, Span span);  // check.py:11609
    // Where the `flat`-th of the first `upto` dimensions' places is, counted in the order the elements of
    // one run would be -- the place an element starts where `upto` is every dimension, and a row's
    // otherwise.
    ir::Value* flat_place(ir::Builder& builder, ir::Value* base, const ir::ArrayType* ty, ir::Value* flat, Span span, std::optional<std::int64_t> upto = {});  // check.py:11615
    // How far from the first element the `flat`-th place of the first `upto` dimensions is, in elements
    // (`_flat_place`).
    ir::Value* flat_offset(ir::Builder& builder, std::span<ir::Value* const> lengths, std::span<ir::Value* const> strides, ir::Value* flat, std::int64_t upto, bool one_run, Span span);  // check.py:11633
    // The index along each dimension of the next place a walk in order reaches, or of the one before where
    // `downward`: the last dimension moves, and one that runs off its end starts again and moves the one
    // before it.
    static std::vector<ir::Value*> odometer(ir::Builder& builder, std::span<ir::Value* const> indices, std::span<ir::Value* const> lengths, bool downward, Span span);  // check.py:11652
    // How far from the first element the place at `indices` is, in elements: each index by its dimension's
    // stride, added up.
    static ir::Value* offset_at(ir::Builder& builder, std::span<ir::Value* const> indices, std::span<ir::Value* const> strides, Span span);  // check.py:11689
    // Lower one index and see to it that it is one the array has.
    ir::Value* checked_index(ir::Builder& builder, const ast::Expr* written, std::optional<std::uint64_t> along, ir::Value* length, Span span, bool one_past = false);  // check.py:11699
    // An index as the word every count is compared and multiplied as.
    ir::Value* as_count(ir::Builder& builder, ir::Value* value, const ir::IntType* found, Span span);  // check.py:11747
    // How far from the first element one place is, counted in elements.
    ir::Value* offset_of(ir::Builder& builder, std::span<const ast::Expr* const> written, const ir::ArrayType* ty, std::span<ir::Value* const> lengths, std::span<ir::Value* const> strides, Span span);  // check.py:11755
    // A count of rows turned into a count of elements.
    ir::Value* by_row(ir::Builder& builder, ir::Value* offset, std::span<ir::Value* const> left, Span span);  // check.py:11785
    // The type of what is left of an array once `taken` indices are given.
    const ir::ArrayType* row_type(const ir::ArrayType* ty, std::int64_t taken);  // check.py:11792
    // The row an array has at a place, where not every index was given.
    ir::Value* row_at(ir::Builder& builder, ir::Value* start, const ir::ArrayType* ty, std::span<ir::Value* const> lengths, std::span<ir::Value* const> strides, ir::Value* offset, std::int64_t taken, Span span);  // check.py:11796
    // Lower `a⟦i⟧`, one element, or `a⟦i…j⟧`, a run of them.
    ir::Value* lower_element(ir::Builder& builder, const ast::Element* expr, const ir::Type* expected);  // check.py:11815
    // The mask an array is being picked with, or nothing where it is not.
    ir::Value* mask_written(ir::Builder& builder, const ast::Element* expr);  // check.py:11920
    // Lower `a⟦m⟧` where `m` is a mask: the things it picked, in order.
    ir::Value* lower_picked(ir::Builder& builder, const ast::Element* expr, ir::Value* base, const ir::ArrayType* ty, ir::Value* mask, const ir::Type* expected);  // check.py:11945
    // Lower `a⟦m⟧ ← v`: write `v` where the mask picked, and nowhere else.
    void assign_picked(ir::Builder& builder, const ast::ElementAssign* stmt, ir::Value* base, const ir::ArrayType* ty, ir::Value* mask);  // check.py:12028
    // Lower `t⟦i⟧`: one member of a tuple, named by where it stands.
    ir::Value* lower_tuple_member(ir::Builder& builder, const ast::Element* expr, ir::Value* base, const ir::TupleType* ty, const ir::Type* expected);  // check.py:12094
    // What `expr` comes to while compiling, where it is a constant.
    std::optional<Folded> constant(const ast::Expr* expr);  // check.py:12135
