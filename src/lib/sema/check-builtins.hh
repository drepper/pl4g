// The checker's methods ported from check.py lines 26513 to 28680, defined in
// check-builtins.cc.  Not a header of its own: included inside the class body of `Checker` (check.hh).

    // What was written as the address, read as one.
    ir::Value* as_an_address(ir::Builder& builder, const ast::Expr* written, std::string_view name);  // check.py:26513
    // Lower `⎕acquire(REF)` and `⎕release(REF, VALUE)`.
    ir::Value* lower_ordered(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:26538
    // `value` as the machine word the kernel reads out of a register.
    ir::Value* as_a_word(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span);  // check.py:26602
    // Lower `⎕narrow(EXPR, ⌜TYPE⌝)`: a value of a narrower type, or why not.
    ir::Value* lower_narrow(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:26619
    // Lower `⎕widen(EXPR, ⌜TYPE⌝)`: the value, as a type with room for it.
    ir::Value* lower_widen(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:26676
    // Lower `⎕narrow(EXPR, ⌜ENUM⌝)`: a number read as one of its values.
    ir::Value* narrowed_to_enum(ir::Builder& builder, const ast::Call* expr, const ir::EnumType* wanted, const ir::Type* expected);  // check.py:26728
    // The number as a value of the enumeration, cut to its width.
    ir::Value* as_enum(ir::Builder& builder, ir::Value* given, const ir::IntType* found, const ir::EnumType* wanted, Span span);  // check.py:26774
    // The value as the narrower type, whether it fits, and why it does not.
    std::tuple<ir::Value*, ir::Value*, ir::Value*> fitted(ir::Builder& builder, ir::Value* given, const ir::IntType* found, const ir::IntType* into, Span span);  // check.py:26789
    // Whether either holds, with a condition that cannot hold dropped.
    ir::Value* either(ir::Builder& builder, ir::Value* one, ir::Value* other, Span span);  // check.py:26827
    // Which condition it was, as a value of the enumeration.
    ir::Value* reason(ir::Builder& builder, ir::Value* under, ir::Value* wrong_sign, Span span);  // check.py:26836
    // `given` read as `into`, the value having been checked to fit it.
    ir::Value* as_wide(ir::Builder& builder, ir::Value* given, const ir::IntType* found, const ir::IntType* into, Span span);  // check.py:26858
    // What an array is an array of, however many dimensions deep.
    const ir::Type* scalar_of(const ir::Type* ty);  // check.py:26875
    // Where an operand is an array, apply the operator element by element.
    ir::Value* walk_operands(ir::Builder& builder, const ast::Expr* expr, std::span<const std::tuple<std::string, ir::Value*>> given, const ir::Type* expected);  // check.py:26887
    // Whether the innermost run can be asked the operator all at once.
    bool all_at_once(const ast::Expr* expr, std::span<const ir::Type* const> wanted, std::span<const std::int64_t> shape);  // check.py:26940
    // Apply the operator to the innermost run of elements all at once.
    ir::Value* run_at_a_time(ir::Builder& builder, const ast::Expr* expr, std::span<const std::string> names, std::span<ir::Value* const> values, std::span<const ir::Type* const> wanted, std::span<const std::int64_t> shape, const ir::Type* expected);  // check.py:26973
    // One operand of a run-at-a-time operation, as a value of `lanes` lanes.
    ir::Value* run_or_one(ir::Builder& builder, ir::Value* value, const ir::Type* want, std::span<const std::int64_t> index, std::int64_t lanes, Span span);  // check.py:27020
    // Where the run that starts at the `at`-th element is, read as one value.
    ir::Value* run_place(ir::Builder& builder, ir::Value* base, const ir::VecType* held, std::int64_t at, Span span);  // check.py:27049
    // How many along each dimension the operands are walked.
    std::optional<std::vector<std::int64_t>> shape_walked(std::span<ir::Value* const> values, std::span<const ir::Type* const> wanted, Span span);  // check.py:27058
    // Lower the operator once, for the element the index names.
    ir::Value* element_answer(ir::Builder& builder, const ast::Expr* expr, std::span<const std::string> names, std::span<ir::Value* const> values, std::span<const ir::Type* const> wanted, std::span<const std::int64_t> index);  // check.py:27085
    // Call `make` once for each element of what was handed it as an array.
    ir::Value* walked(ir::Builder& builder, const ir::FuncType* ty, std::function<ir::Value*(std::span<ir::Value* const>, Span)> make, std::span<ir::Value* const> args, const ast::Call* expr, const ir::Type* expected);  // check.py:27100
    // How many along each dimension the walk goes, outermost first.
    std::optional<std::vector<std::int64_t>> walking_shape(const ir::FuncType* ty, std::span<ir::Value* const> args, const ast::Call* expr);  // check.py:27135
    // What is left of an array once its outermost dimension comes off.
    const ir::Type* one_less(const ir::ArrayType* ty);  // check.py:27171
    // Make the calls one dimension at a time, writing the answers in order.
    void each_of(ir::Builder& builder, const ir::FuncType* ty, std::function<ir::Value*(std::span<ir::Value* const>, Span)> make, std::span<ir::Value* const> args, std::span<const std::int64_t> shape, ir::Value* place, std::int64_t at, Span span);  // check.py:27177
    // The `index`-th of an argument being walked, or the argument itself.
    ir::Value* one_of(ir::Builder& builder, ir::Value* value, const ir::Type* wanted, std::int64_t index, Span span);  // check.py:27203

    // -- each: `¨` ---------------------------------------------------------------
    // A name for what a walk is at, which no program can write: `¨` is no letter.
    std::string each_name();  // check.py:27225
    // Bind a name the compiler made up, which nothing reports as unread.
    void bind_hidden(ir::Builder& builder, std::string_view name, ir::Value* value, Span span, const ir::Type* placed_as = nullptr);  // check.py:27231
    // The array `¨` walks, or nothing where it cannot walk `value`.
    const ir::ArrayType* walkable(ir::Value* value, std::int64_t levels, Span span);  // check.py:27243
    // Bind `name` to the element `at` places into the run at `start`.
    void bind_element(ir::Builder& builder, std::string_view name, ir::Value* start, const ir::Type* element, ir::Value* at, Span span);  // check.py:27258
    // What a walk `levels` deep is at, `at` places in: an element, or a row.
    ir::Value* walk_at(ir::Builder& builder, const ir::ArrayType* ty, std::int64_t levels, ir::Value* at, ir::Value* start, std::span<ir::Value* const> lengths, std::span<ir::Value* const> strides, Span span);  // check.py:27271
    // Lower a value written with `¨`: what is done to each element, as an array.
    ir::Value* lower_each(ir::Builder& builder, const ast::Expr* expr, const ir::Type* expected);  // check.py:27284
    // Lower a call with an argument written with `¨`: one call for each.
    ir::Value* lower_each_call(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:27305
    // Write `template` out once for each element and gather the answers.
    ir::Value* each_built(ir::Builder& builder, const ast::Expr* expr, std::span<const std::tuple<std::string, ir::Value*, const ir::ArrayType*, std::int64_t, Span>> sources, std::span<const std::tuple<std::string, ir::Value*>> fixed, const ast::Expr* template_, const ir::Type* expected);  // check.py:27347
    // Lower `ps¨.age ← v`: the same write to each element, in a loop.
    void assign_each(ir::Builder& builder, const ast::Stmt* stmt, ir::Function* func);  // check.py:27437
    // See to it that what is walked beside an array is as long as it is.
    void each_agree(ir::Builder& builder, const ast::Expr* other, ir::Value* length, const ir::ArrayType* ty, Span span);  // check.py:27554
    // Lower a list of written things left to right, spreads and all.
    std::optional<std::vector<ir::Value*>> one_by_one(ir::Builder& builder, std::span<const ast::Expr* const> written, std::optional<std::span<const ir::Type* const>> wanted, std::optional<std::string> callee = {});  // check.py:27576
    // The values `⁂x` stands for, one each, or nothing where it stands for no such list.
    std::optional<std::vector<ir::Value*>> taken_one_each(ir::Builder& builder, const ast::Spread* spread);  // check.py:27629
    // Refuse a call to a compiler-provided function from code that runs.
    void only_at_build(ir::Function* func, Span span);  // check.py:27684
    // The function a call names, or nothing where it does not name one.
    ir::Function* callee(const ast::Expr* expr);  // check.py:27695
    // The type another module exports under this name, asked quietly.
    const ir::Type* type_exported(const ast::Member* expr);  // check.py:27724
    // The generic definition a callee names, by a bare name or through a module.
    Generic* generic_named(const ast::Expr* callee);  // check.py:27739
    // What a path naming something inside a type stands for, or nothing.
    Held member_named(const ast::Expr* callee);  // check.py:27762
    // The type and the name a path names, where it is one of those two shapes.
    std::optional<std::tuple<std::string, std::string>> a_member_path(const ast::Expr* callee);  // check.py:27784
    // The function another module exports under this name.
    ir::Function* callee_of_module(const ast::Member* expr);  // check.py:27803
    // Whether reading this name is copying a value that is never copied.
    bool would_copy(Local* local, const ast::NameRef* ref);  // check.py:27848
    // Lower a reference to a name.
    ir::Value* lower_name(ir::Builder& builder, const ast::NameRef* ref, const ir::Type* expected);  // check.py:27870
    // Lower `expr` where a value of `expected` is wanted.
    ir::Value* lower_into(ir::Builder& builder, const ast::Expr* expr, const ir::Type* expected, Span span);  // check.py:27979
    // A value promising more than is wanted, read as what is wanted.
    ir::Value* shorter_life(ir::Builder& builder, ir::Value* value, const ir::Type* expected, Span span);  // check.py:28025
    // Lower `expr` where an array of no stated length is wanted.
    ir::Value* spread(ir::Builder& builder, const ast::Expr* expr, const ir::ArrayType* expected, Span span);  // check.py:28051
    // Keep a value whose type matches; stand in for one whose type does not.
    ir::Value* as_declared(ir::Value* value, const ir::Type* expected);  // check.py:28076
    // How many dimensions come off `found` before it is `wanted`, or nothing.
    std::optional<std::int64_t> walks_down_to(const ir::Type* found, const ir::Type* wanted);  // check.py:28086
    // What is wanted of something that cannot itself be a result.
    const ir::Type* aiming_at(const ir::Type* expected);  // check.py:28104
    // Whether a value of `found` stands where `expected` is wanted.
    bool accepts(const ir::Type* expected, const ir::Type* found);  // check.py:28119
    // The record of what `node`'s body does, for `func` being lowered.
    Marks* marks_for(const ast::FuncDef* node, ir::Function* func, Generator* walked);  // check.py:28160
    // Register a slot a function type stands in, by `key`, and the mark `@[impure]` written on its type
    // where it is.
    void slot(const SlotKey& key, const ast::TypeExpr* written, std::string_view what, bool outside);  // check.py:28183
    // Note that what `written` comes to is given to the slot `key`.
    void flow(const SlotKey& key, const ast::Expr* written);  // check.py:28195
    // Where a function value given somewhere comes from, as far as whether it may change things goes -- a
    // slot, a named function, a lambda -- and nothing where that cannot be told, which is then taken to.
    Held source_of(const ast::Expr* written);  // check.py:28201
    // Register the slots the fields of a record defined here are.
    void field_slots(const ast::Node* node);  // check.py:28230
    // Register the slot a variable of function type is, and what it is given.
    void variable_slot(const ast::VarDef* stmt);  // check.py:28240
    // Note what a call gives each parameter of its callee.
    void flows_of_call(const ast::Call* expr, ir::Function* func);  // check.py:28252
    // Register the slots a definition's parameters and answer are, and bind each parameter's name to its
    // slot.
    void parameter_slots(const ast::FuncDef* node, ir::Function* func);  // check.py:28261
    // Refuse `@[impure]` and `@[io]` written where nothing needs them (4933).
    void check_marks();  // check.py:28275
    // Report a call whose callee changes more than this function may.
    void what_it_changes(ir::Function* func, Span span);  // check.py:28356
    // Report a change that outlives the call, where the function is pure.
    void an_effect(std::int64_t which, Span span, Held through = {});  // check.py:28387
    // Whether a place is storage this call made and this call will lose.
    bool made_here(ir::Value* place);  // check.py:28434
    // Whether a table is one this call made: a literal, or what a set operator answers, written in this
    // body.
    bool table_made_here(ir::Value* table);  // check.py:28463
    // A table written at `span` -- or, where `filler` names a function, handed to one that writes it: an
    // effect unless it is this call's own.
    void written_table(ir::Value* table, Span span, std::string_view filler = "");  // check.py:28485
    // Report writing a table somebody else may hold, directly or by handing it to a function that fills
    // it.
    void table_not_ours(Span span, std::string_view filler);  // check.py:28494
    // Ask of every table a call hands where a writable one is wanted whether it is the caller's own: the
    // callee may fill it, which is no effect of the callee's -- its signature says so -- but is the
    // caller's where the table is somebody else's.
    void tables_handed(std::span<const ir::Type* const> params, std::span<ir::Value* const> args, std::string_view name, Span span);  // check.py:28502
    // Whether `table` is what `func` was handed as a writable table: one of its parameters, whose type
    // says the body may fill it.
    bool through_a_parameter(ir::Value* table, ir::Function* func);  // check.py:28512
    // Answer what writing a table asked where branches met, now that every edge of `func` is there: the
    // table is this call's own where every edge into the block hands it one -- round a loop, what the loop
    // started with -- and somebody else's where any edge hands it anything else.
    void settle_tables(ir::Function* func);  // check.py:28521
    // Report a type that does not match what the context requires.
    void report_mismatch(Span span, const ir::Type* found, const ir::Type* expected);  // check.py:28564

    // -- program level -----------------------------------------------------------
    // Check the properties the whole program must have.
    void check_program();  // check.py:28613
    // Report a variable at the top level that is written and never read.
    void report_unread_variables();  // check.py:28621
    // Report what a variable's definition asserted and nothing raised.
    void settle_global(Global* entry);  // check.py:28649
    // Which variables the program reads, and how often it writes each.
    std::tuple<KeySet, Dict<std::int64_t, std::int64_t>> variable_uses();  // check.py:28659
