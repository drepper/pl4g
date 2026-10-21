// The checker's methods ported from check.py lines 23413 to 26512, defined in
// check-operators.cc.  Not a header of its own: included inside the class body of `Checker` (check.hh).

    // Lower `A ⧺ B` where the two are strings.
    ir::Value* joined_text(ir::Builder& builder, const ast::Binary* expr, ir::Value* left, ir::Value* right, const ir::Type* expected);  // check.py:23413
    // What joining these two answers with, or nothing where they do not join.
    const ir::ArrayType* joined_type(const ast::Binary* expr, ir::Value* left, ir::Value* right);  // check.py:23457
    // Put the elements of `side` into `place`, starting at the `at`-th.
    void copied(ir::Builder& builder, ir::Value* side, const ir::ArrayType* ty, ir::Value* place, const ir::Type* element, std::int64_t at, Span span);  // check.py:23493
    // Lower an operator written between two operands.
    ir::Value* lower_binary(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected);  // check.py:23518
    // Whether a value of `ty` may stand on one side of `op`.
    bool operand_type_stands(ast::BinaryOp op, const ir::Type* ty);  // check.py:23686
    // Report an operation that can be seen to fault, and say whether it was.
    bool answer_is_already_known(const ast::Binary* expr, const ir::Type* ty, ir::Value* left, ir::Value* right);  // check.py:23707
    // Report a floating-point operation that can be seen to fault.
    bool float_answer_is_already_known(const ast::Binary* expr, const ir::FloatConst* left, const ir::FloatConst* right);  // check.py:23759
    // Lower an operator written before its operand.
    ir::Value* lower_unary(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected);  // check.py:23788
    // Lower one of the four roundings: the whole number a number goes to.
    ir::Value* lower_rounding(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected);  // check.py:23854
    // Lower `#x`: how many things `x` is made of.
    ir::Value* lower_length(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected);  // check.py:23899
    // How many things a value is made of, or nothing where it is one thing.
    ir::Value* counted(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span);  // check.py:23937
    // Lower `⌈x` and `⌊x`: the largest or the smallest of what it holds.
    ir::Value* lower_extremum(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected);  // check.py:23965
    // Lower `⌈⌜u8⌝`: the largest or the smallest value a type has.
    ir::Value* end_of_a_type(const ast::Unary* expr, const ast::Lifted* lifted, const ir::Type* expected);  // check.py:24001
    // The largest or smallest of what a value holds, or nothing where it holds nothing that can be
    // compared.
    ir::Value* extremum_of(ir::Builder& builder, const ast::Unary* expr, ir::Value* value, const ir::Type* ty);  // check.py:24042
    // Whether a comparison puts two of these in an order, which is what being the largest of several
    // means.
    bool ordered(const ir::Type* ty);  // check.py:24073
    // The largest or smallest of values known one by one.
    ir::Value* folded(ir::Builder& builder, const ast::Unary* expr, std::span<ir::Value* const> values, const ir::Type* element);  // check.py:24078
    // The largest or smallest of an array whose shape its type states.
    ir::Value* extremum_of_array(ir::Builder& builder, const ast::Unary* expr, ir::Value* value, const ir::ArrayType* ty);  // check.py:24087
    // The largest or smallest of something whose count is not known while compiling, which is a loop.
    ir::Value* extremum_walked(ir::Builder& builder, const ast::Unary* expr, ir::Value* value, const ir::Type* ty);  // check.py:24129
    // What one turn of a walk offers to be compared.
    ir::Value* offered(ir::Builder& builder, const Iteration& found, std::span<ir::Value* const> state, const ir::Type* ty, const ir::Type* element, Span span);  // check.py:24197
    // What one of these operators answers with, given what it is asked of.
    const ir::Type* extremum_type(const ir::Type* of);  // check.py:24205
    // Which of the four instructions one of these operators is, over this type: the two of them times
    // signed and unsigned, a character being the unsigned number it is stored as.
    ir::BinOp extremum_op(const ast::Unary* expr, const ir::Type* element);  // check.py:24229
    // Lower `⍴x`: how many there are along each of its dimensions.
    ir::Value* lower_shape(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected);  // check.py:24238
    // Lower `¬`, which answers the opposite of what its operand says.
    ir::Value* lower_not(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected);  // check.py:24271
    // The type an expression says it has, without lowering it.
    const ir::Type* hint_of(const ast::Expr* expr);  // check.py:24295
    // The type a name has, without reporting one that is not bound.
    const ir::Type* type_of_name(std::string_view name);  // check.py:24432
    // The type a floating-point literal has, from its suffix or its place.
    const ir::FloatType* float_literal_type(const ast::FloatLit* expr, const ir::Type* expected);  // check.py:24446
    // The type an integer literal has, from its suffix or from the context.
    const ir::IntType* literal_type(const ast::IntLit* expr, const ir::Type* expected);  // check.py:24473
    // Lower `⎕ones(N)` and `⎕lead(N)`: how many of a value's bits are set, and how many zeroes stand above
    // the highest one that is.
    ir::Value* lower_bit_count(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:24505
    // Lower `⎕ord(C)` and `⎕chr(N)`: the two conversions between a code point and the number Unicode gave
    // it.
    ir::Value* lower_code_point(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:24549
    // Lower `[a, b, c]`: a list written down.
    ir::Value* lower_list(ir::Builder& builder, const ast::ListLit* expr, const ir::Type* expected);  // check.py:24596
    // Room in the arena for `count` elements of a list, as a place to put them: the list's own room, with
    // the header saying how much it is, and nowhere for no elements at all.
    ir::Value* room_for(ir::Builder& builder, const ir::Type* element, std::variant<ir::Value*, std::int64_t> count, Span span);  // check.py:24668
    // A string written down: where its bytes are, and how many there are.
    ir::Value* written_text(ir::Builder& builder, std::string_view text, Span span);  // check.py:24687
    // The variable of the image text written down is the bytes of, made once per distinct text.
    ir::GlobalVar* text_variable(std::string_view text);  // check.py:24708
    // The code point `value`, or a report where there is no such code point.
    ir::Value* code_point(ir::Builder& builder, std::int64_t value, Span span);  // check.py:24725
    // Lower `⎕wrap(EXPR)`: the expression, with its operators wrapping.
    ir::Value* lower_wrap(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:24739
    // The operation to emit for `op` where it stands, wrapping or not.
    ir::BinOp wrapped(ir::BinOp op);  // check.py:24764
    // Lower a call, checking it against what the function takes and gives.
    ir::Value* lower_call(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:24768
    // `v.f(args)` as the call it stands for, `T.f(v, args)`, or nothing.
    Held through_a_value(ir::Builder& builder, const ast::Call* expr);  // check.py:24882
    // Report that the type of what a call was written on has nothing by the name it asked for, and answer
    // that the call is refused.
    Held no_such_member(const ast::Member* callee, const ir::Type* ty);  // check.py:24952
    // A path from this file to the function `name` inside `ty`, or nothing.
    const ast::Expr* path_to_member(const ir::Type* ty, std::string_view name, const ast::Member* at);  // check.py:24959
    // How a function inside a type takes its first parameter: nothing for by value, otherwise whether the
    // reference it takes may write.
    std::optional<bool> first_taken(Held found);  // check.py:25002
    // With the names the file that wrote `defined` could see in force.
    [[nodiscard]] InScope in_scope_of(Held defined);  // check.py:25015
    // The definition with type parameters a call names, where it names one: by a bare name, or through the
    // module that exports it.
    NamedType* template_named(const ast::Expr* callee);  // check.py:25025
    // Lower a record or a variant of a type with type parameters, written out.
    ir::Value* lower_generic_value(ir::Builder& builder, const ast::Call* expr, NamedType* template_, const ir::Type* expected);  // check.py:25038
    // Lower a call whose callee is already known to be `func`.
    ir::Value* through(ir::Builder& builder, const ast::Call* expr, ir::Function* func, const ir::Type* expected);  // check.py:25111
    // Every parameter's value, in the order the function takes them.
    std::optional<std::vector<ir::Value*>> given_arguments(ir::Builder& builder, const ast::Call* expr, ir::Function* func);  // check.py:25163
    // Lower `⎕drop(x)` and `⎕unit(x, ⌜UNIT⌝)`, which cross between units.
    ir::Value* lower_unit_call(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:25272
    // `given`, read as `wanted`, which differs from its type only in a unit.
    ir::Value* as_united(ir::Builder& builder, ir::Value* given, const ir::Type* wanted, const ir::Type* expected, Span span);  // check.py:25319
    // The unit lifted out of the program between `⌜` and `⌝`.
    const ir::Unit* unit_lifted(const ast::Lifted* written);  // check.py:25331
    // One written unit, read out of what the parser made of it.
    const ir::Unit* unit_expression(const ast::Expr* expr, std::int64_t sign);  // check.py:25348
    // Lower `⎕sc@NAME`: the number that call has where this is built for.
    ir::Value* syscall_number(const ast::NameRef* ref);  // check.py:25378
    // Lower `Point(.x ← 1f64, .y ← 2f64)`: a value of a record.
    ir::Value* lower_record(ir::Builder& builder, const ast::Call* expr, const ir::Type* ty, const ir::Type* expected);  // check.py:25398
    // Lower `Number(.whole ← 5i64)`: a value of a sum.
    ir::Value* lower_sum(ir::Builder& builder, const ast::Call* expr, const ir::Type* ty, const ir::Type* expected);  // check.py:25458
    // Where the tag of a sum is: after its largest part.
    ir::Value* tag_place(ir::Builder& builder, ir::Value* place, const ir::SumType* ty, Span span);  // check.py:25508
    // Write what a part carries at the start of the place holding a sum.
    void payload_into(ir::Builder& builder, ir::Value* place, ir::Value* value, const ir::Type* held, Span span);  // check.py:25523
    // Copy the elements of the array `from_` into those of `into`, one for one, where the two have one
    // shape.
    bool copy_elements(ir::Builder& builder, ir::Value* into, const ir::ArrayType* into_ty, ir::Value* from_, const ir::Type* from_ty, Span span);  // check.py:25540
    // Copy `count` elements of `element` from one array to another, laid out as each says -- where the
    // first is, how many along each dimension and how far apart, and whether that is one run -- which may
    // overlap, `v⟦0…3⟧ ← v⟦1…4⟧`, as `memmove`'s may.
    void move_elements(ir::Builder& builder, const Laid& into_shape, const Laid& from_shape, const ir::Type* element, ir::Value* count, std::optional<std::int64_t> known, Span span);  // check.py:25601
    // A loop copying `count` elements, from the last if `downward`.
    void element_by_element(ir::Builder& builder, const Laid& into_shape, const Laid& from_shape, const ir::Type* element, ir::Value* count, bool downward, Span span);  // check.py:25647
    // Copy `size` bytes, in the largest pieces that fit.
    void copy_bytes(ir::Builder& builder, ir::Value* into, ir::Value* from_, std::int64_t size, Span span);  // check.py:25694
    // One piece of a copy, as a pointer to a value of `part`.
    ir::Value* byte_place(ir::Builder& builder, ir::Value* place, const ir::Type* part, std::int64_t offset, Span span);  // check.py:25711
    // Read what a part carries out of the place holding a sum.
    ir::Value* payload_from(ir::Builder& builder, ir::Value* place, const ir::Type* held, Span span);  // check.py:25722
    // Lower `⎕syscall(NUMBER, ARG...)`: what the kernel answered.
    ir::Value* lower_syscall(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:25739
    // Lower `⎕at(ADDRESS, ⌜TYPE⌝)` and `⎕span(ADDRESS, COUNT, ⌜TYPE⌝)`.
    ir::Value* lower_place_at(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:25779
    // Lower `⎕bytes(TEXT)`: what a string is made of.
    ir::Value* lower_bytes(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:25865
    /// Lower `⎕text(BYTES)`: text from bytes, where they are UTF-8 -- a copy of them in the arena in
    /// force, or where the first byte is that is not part of a well-formed sequence.
    ir::Value* lower_text(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);
    /// The view `view` of one dimension as one run: itself where its stride is one, and a copy
    /// otherwise, in the arena in force.
    ir::Value* as_one_run(ir::Builder& builder, ir::Value* view, const ir::ArrayType* ty, Span span);
    /// `value` as the view `wanted`, where it is one or can be read as one, and nothing where it
    /// cannot: an array letting its shape go, or a list of the same elements, which is lent for as
    /// long as the view lives.  What `written` is says which list that is.
    ir::Value* as_a_view(ir::Builder& builder, const ast::Expr* written, ir::Value* value, const ir::ArrayType* wanted,
                         Span span);
    // Lower `⎕typename(⌜T⌝)`: the type written out, as text.
    ir::Value* lower_typename(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:25904
    // Lower `⎕fields(v)`: a record's fields as a tuple of name and value.
    ir::Value* lower_fields(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:25928
    // The type `⎕fields(⌜T⌝)` asks about, where it asks about a type rather than a value: an enumeration's
    // or a record's; nothing for anything else.
    const ir::Type* fields_of_a_type(const ast::Expr* expr);  // check.py:26004
    // `⎕fields(⌜E⌝)`: an enumeration's values, each with its name, as a tuple of pairs -- in the order the
    // definition wrote them, an alias included, for the reason a record's fields come in the order they
    // were written.
    ir::Value* members_of(ir::Builder& builder, const ir::EnumType* ty, const ast::Call* expr, const ir::Type* expected);  // check.py:26020
    // Lower `⎕one_run(v)`: the elements of `v` as one run.
    ir::Value* lower_one_run(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:26044
    // ``__pl4g_one_run.T(arena, v)``: the elements of the view `v` copied into room from `arena` as one
    // run, and that run. Built once for a type, and never written out where it is called.
    ir::Function* one_run_function(const ir::ArrayType* ty);  // check.py:26111
    // Lower `⎕str(CHAR)`: a string of one character.
    ir::Value* lower_str_of(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:26144
    // Lower `⎕empty(ARENA)`: everything it holds, given back at once.
    ir::Value* lower_empty(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:26193
    // The arena an `⎕empty` the compiler put off names: one this body made.
    Local* arena_owned(const ast::Expr* written);  // check.py:26223
    // Where the arena an expression names keeps its state, or nothing.
    ir::Value* arena_place(ir::Builder& builder, const ast::Expr* written);  // check.py:26235
    // Whether a program defined this operator with an arena as its last operand.
    bool operator_takes_an_arena(const ast::Expr* expr);  // check.py:26251
    // Where what is being lowered takes its room from.
    ir::Value* out_of(ir::Builder& builder, Span span);  // check.py:26271
    // Check an array of text put in a field of a record.
    void owning_field(const ast::Expr* written, const ir::Type* record, std::string_view name, const ir::Type* held);  // check.py:26285
    // Whether a name stands for a reference to an arena rather than for one.
    bool held_by_reference(const ast::Expr* written);  // check.py:26307
    // Lower `EXPR in NAME`: which arena the expression takes its room from.
    ir::Value* lower_allocated(ir::Builder& builder, const ast::Allocated* expr, const ir::Type* expected);  // check.py:26317

    // -- is handed on as the reference it is. ------------------------------------
    // Lower `… in ⎕static`: something made while compiling, in the image.
    ir::Value* lower_static(ir::Builder& builder, const ast::Expr* whole, const ast::Expr* written, const ir::Type* expected);  // check.py:26411
    // One word of something in the image, as the value it is.
    ir::Value* from_the_image(ir::Builder& builder, const ir::Value* part, const ir::Type* ty, Span span);  // check.py:26469
    // Lower `⎕address(REF)`: a place as the number it is.
    ir::Value* lower_address_of(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:26481
