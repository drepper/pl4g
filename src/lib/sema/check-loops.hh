// The checker's methods ported from check.py lines 18053 to 20792, defined in
// check-loops.cc.  Not a header of its own: included inside the class body of `Checker` (check.hh).

    // -- what a loop can take its values from ------------------------------------
    // Lower `comptime foreach`, which is the body written out once per turn.
    ir::Value* written_out(ir::Builder& builder, const ast::ForEach* stmt, ir::Function* func, const ir::Type* expected, bool produces);  // check.py:18055
    // `comptime foreach f := ⎕fields(⌜T⌝)` over a record type: the body once per field, `f⟦0⟧` its name
    // and `f⟦1⟧` its type.
    ir::Value* written_out_per_field(ir::Builder& builder, const ast::ForEach* stmt, ir::Function* func, const ir::ProductType* ty);  // check.py:18105
    // The type `f⟦1⟧` stands for, where `f` is a turn of `comptime foreach` over a record type's fields;
    // nothing for anything else.
    const ir::Type* field_type_named(const ast::Expr* expr);  // check.py:18136
    // What the loop's expression turns out to be, as a thing to walk.
    std::optional<Iteration> iteration_over(ir::Builder& builder, const ast::ForEach* stmt);  // check.py:18149
    // What `⎕enumerate` was asked to walk and what to count from, or nothing where the loop's expression
    // is not one of those.
    std::optional<std::tuple<const ast::Expr*, const ast::Expr*>> enumerating(const ast::Expr* written);  // check.py:18163
    // Walk something and count the turns, which is the two as one.
    std::optional<Iteration> enumerated(ir::Builder& builder, const ast::ForEach* stmt, const std::tuple<const ast::Expr*, const ast::Expr*>& counted);  // check.py:18178
    // What the loop's expression is as a thing to walk, without counting.
    std::optional<Iteration> iteration_of(ir::Builder& builder, const ast::ForEach* stmt);  // check.py:18246
    // What is wanted of a loop's expression, given what a turn is declared to be.
    const ir::Type* holding(const ir::Type* declared, const ast::Expr* iterable);  // check.py:18298
    // Walk the whole numbers a range stands for.
    std::optional<Iteration> over_a_range(ir::Builder& builder, const ast::ForEach* stmt);  // check.py:18335
    // Walk an array along its outermost dimension.
    Iteration over_an_array(ir::Builder& builder, ir::Value* value, const ir::ArrayType* ty, Span span);  // check.py:18359
    // Walk what a mask picks out of an array, where it stands.
    std::optional<Iteration> over_a_pick(ir::Builder& builder, const ast::Element* written, ir::Value* base, const ir::ArrayType* ty, Mask mask, bool checked = true);  // check.py:18395
    // Work a loop's mask out a place at a time where it is written so.
    bool pick_per_place(ir::Builder& builder, const ast::Element* expr, ir::Value* base, const ir::ArrayType* ty);  // check.py:18500
    // Walk a value of a type a loop walks as it is, or say it is not one.
    std::optional<Iteration> over_a_value(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span);  // check.py:18665
    // Where an `each` inside `ty` that is not a generator was written, which is what a loop over a value
    // of it would have walked had it been one.
    std::optional<Span> each_written(const ir::Type* ty, Span span);  // check.py:18699
    // The path to the generator a value of `ty` is walked by, where its type is one of the program's own
    // with one -- `T.each`, or `m.T.each` for a type another module exports -- or a reference to one.
    const ast::Expr* walking_each(const ir::Type* ty, Span span);  // check.py:18717
    // What `expr` is, as far as can be said before it is lowered: what `_hint_of` says, and besides a
    // field of a record and an element of an array, which is what a walk is most often written over.
    const ir::Type* said_to_be(const ast::Expr* expr);  // check.py:18733
    // `T.each(base)`, the call `base.each()` stands for, where `ty` -- what `base` is -- has an `each`;
    // nothing where it has none, and `_REFUSED` where it has one the value cannot be handed to.
    Held each_call(ir::Builder& builder, const ast::Expr* base, const ir::Type* ty, Span span);  // check.py:18755
    // The walk `T.each(value)` describes, where `ty` is a type of the program's own with an `each`: the
    // generator's call, lowered where a walk is wanted, so that it is what a name is bound to, what is
    // handed on and what is started, as a call written out would be.
    Iterable* each_walk(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span);  // check.py:18778
    // `written` -- or what `in` is written after -- as `T.each(…)` where it is said to be a value of a
    // type with an `each`, so that `foreach x := v`, `yield ⇉ v` and `yield ⇉ v in a` walk the generator's
    // call as one written out would: written into the loop where it can be. Asked of what the value is
    // said to be before it is lowered; a value known only after is walked through calls (`_over_a_value`).
    const ast::Expr* called_each(ir::Builder& builder, const ast::Expr* written);  // check.py:18796
    // What a turn of a pick out of `ty` gives: an element where the mask covers every dimension, a row
    // where it covers the leading ones.
    const ir::Type* picked_element(const ir::ArrayType* ty, Held mask);  // check.py:18812
    // Lower `let q: ⇉T = …` and `let q: ⇉ = …`: a name for a walk.
    void lower_iterator_definition(ir::Builder& builder, const ast::VarDef* node, const ir::IterType* declared);  // check.py:18819
    // Lower `let a: ⟦⟧ = …`: an array of whatever the value gives, built.
    void lower_array_definition(ir::Builder& builder, const ast::VarDef* node, const ast::ArrayTypeRef* written);  // check.py:18860
    // What a name bound to an array stands for: room of its own holding a copy, where its type says the
    // shape and the elements could otherwise change under it or through it.
    ir::Value* owned_array(ir::Builder& builder, const ast::VarDef* node, ir::Value* value);  // check.py:18919
    // Bind `node`'s name to a walk, lending it what the walk reads.
    void bind_iterator(ir::Builder& builder, const ast::VarDef* node, Held walked, const ir::IterType* declared);  // check.py:18959
    // The names of arrays and collections `expr` reads, which a walk of it goes on reading after it was
    // described.
    std::vector<std::string> names_read(const ast::Expr* expr);  // check.py:19005
    // Say in the report log what became of an iterator.
    void report_iterator(std::string_view name, std::string_view reason, Span span);  // check.py:19031
    // Refuse writing a name an iterator in scope reads, and say whether that was refused.
    bool lent_to_an_iterator(const ast::Expr* expr, Span where);  // check.py:19035
    // A name bound to a walk, where it is mentioned.
    ir::Value* iterator_named(ir::Builder& builder, const ast::NameRef* ref, Local* local, const ir::Type* expected);  // check.py:19051
    // The walk a name stands for, started afresh.
    std::optional<Iteration> walk_of(ir::Builder& builder, Iterable* found, Span span);  // check.py:19070
    // The array a walk would give, where an array is asked for.
    ir::Value* built_from(ir::Builder& builder, std::string_view name, Iterable* found, Span span, const ir::Type* expected);  // check.py:19087
    // The mask of a pick as an array of truth values, asking it a place at a time where that is how it was
    // described.
    ir::Value* mask_built(ir::Builder& builder, Iterable* found);  // check.py:19112

    // -- handing an iterator on --------------------------------------------------
    // `value` as the words it is made of, an array of a stated shape as where its elements are.
    std::vector<ir::Value*> words_of(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span);  // check.py:19141
    // The types of the words `_words_of` makes of a value of `ty`.
    std::vector<const ir::Type*> word_types(const ir::Type* ty);  // check.py:19168
    // A value of `ty` put back together from its words.
    ir::Value* from_words(ir::Builder& builder, std::span<ir::Value* const> words, const ir::Type* ty, Span span);  // check.py:19174
    // An iterator as a value, where a call takes one.
    ir::Value* handed_on(ir::Builder& builder, std::string_view name, Iterable* found, const ir::IterType* wanted, Span span);  // check.py:19190
    // `expr` where an iterator is wanted: a parameter of `⇉T`.
    ir::Value* as_an_iterator(ir::Builder& builder, const ast::Expr* expr, const ir::IterType* wanted, Span span);  // check.py:19246
    // Write `value` at `place` word by word, as the description is.
    void words_into(ir::Builder& builder, ir::Value* place, ir::Value* value, const ir::Type* ty, Span span);  // check.py:19297
    // A value of `ty` read back from `place` word by word.
    ir::Value* words_from(ir::Builder& builder, ir::Value* place, const ir::Type* ty, Span span);  // check.py:19307

    // -- generators --------------------------------------------------------------
    // What a generator's argument record holds: its arguments, and after them room for what a resume hands
    // in, where it takes something back, and for what it answers when it ends, where it answers something.
    std::vector<const ir::Type*> record_types(Generator* found);  // check.py:19321
    // Where in the argument record at `args` what a resume hands in is.
    ir::Value* given_place(ir::Builder& builder, ir::Value* args, Generator* found, Span span);  // check.py:19329
    // Where in the argument record at `args` what it answers when it ends is.
    ir::Value* finish_place(ir::Builder& builder, ir::Value* args, Generator* found, Span span);  // check.py:19338
    // How a generator's arguments lie in the record it reads them from: the record, and where each
    // argument's first word is.
    std::tuple<const ir::TupleType*, std::vector<std::int64_t>> argument_record(std::span<const ir::Type* const> params);  // check.py:19346
    // A generator's arguments, read out of the record at `place`.
    std::vector<ir::Value*> arguments_from(ir::Builder& builder, ir::Value* place, std::span<const ir::Type* const> params, Span span);  // check.py:19356
    // A generator's arguments, written into the record at `place`.
    void arguments_into(ir::Builder& builder, ir::Value* place, std::span<const ir::Type* const> params, std::span<ir::Value* const> values, Span span);  // check.py:19370
    // Lower `yield v` in a generator's body: write it out, and stop.
    void lower_yield(ir::Builder& builder, const ast::Yield* stmt, ir::Function* func);  // check.py:19382
    // `yield v` where a value is wanted: hand `v` out, stop, and answer what the next `⎕resume` handed in
    // -- read from the argument record, where the resume wrote it.
    ir::Value* lower_yield_value(ir::Builder& builder, const ast::YieldValue* expr, const ir::Type* expected);  // check.py:19413
    // `yield ⇉ g(…)` where a value is wanted: hand the walk to `g` and, once it has nothing more, answer
    // what it answered when it ended -- read from its argument record, where its `return` wrote it.
    ir::Value* lower_delegated_value(ir::Builder& builder, const ast::YieldValue* expr, const ir::Type* expected);  // check.py:19442
    // Stop the generator's body here, and go on after it when resumed -- or, resumed with nowhere to write
    // to, finish: what was put off up to here runs, and there is nothing more.
    void stopped(ir::Builder& builder, bool delegates, Span span);  // check.py:19471
    const ir::TupleType* delegation();  // check.py:19491
    // `yield ⇉ g(…)` in a body split at its stops: hand the walk to `g` in one step, and say whether it
    // was a generator's call that could be.
    bool delegated(ir::Builder& builder, const ast::Yield* stmt);  // check.py:19494
    // The generator a call names, where it names one: by a bare name, or through the module that exports
    // it.
    Generator* generator_named(const ast::Expr* callee);  // check.py:19571
    // The arguments of a call to a generator, worked out -- and, where it has type parameters, what they
    // read off them and the instance that makes.
    std::optional<std::tuple<Generator*, std::vector<ir::Value*>>> generator_arguments(ir::Builder& builder, const ast::Call* expr, Generator* found);  // check.py:19589
    // A call to a generator: the walk it describes, not a value.
    ir::Value* lower_generator_call(ir::Builder& builder, const ast::Call* expr, Generator* found, const ir::Type* expected);  // check.py:19652
    // Whether `found`'s body walks a generator that walks `found`, or `found` itself -- a frame that would
    // have to hold itself.
    bool generator_reaches_itself(Generator* found);  // check.py:19680
    // Report that `found` walks itself (4675), once for each place: a body written into a loop is lowered
    // there as well as on its own, and what is wrong with it is wrong once.
    void walks_itself_said(Generator* found, Span span);  // check.py:19706
    // Note that the generator whose body is being lowered walks `found` through calls, and say whether
    // that makes a ring: a generator walking itself, directly or through others -- one reached by a call,
    // or by a value whose type's `each` it is, which only lowering finds.
    bool walks_itself(Generator* found);  // check.py:19715
    // `g(…) in a`: a walk of a generator whose frame, when it is walked through calls, is in the arena `a`
    // rather than in this call's room.
    ir::Value* generator_in(ir::Builder& builder, const ast::Allocated* expr, ir::Value* place, const ir::Type* expected);  // check.py:19729
    // `⎕start(w)`: a walk under way, to be driven by `⎕resume`.
    ir::Value* lower_start(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:19764
    // `⎕resume(c)` and `⎕resume(c, u)`: the next turn of a walk under way, or the failure once it has
    // nothing more -- handing `u` in first, to be what the `yield` it stopped at answers, where it takes
    // something.
    ir::Value* lower_resume(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected);  // check.py:19878
    // A generator's call as an iterator value, walked through calls.
    ir::Value* generator_described(ir::Builder& builder, std::string_view name, Iterable* walk, const ir::IterType* wanted, Span span, bool driven = false);  // check.py:19932
    // What a walk of `found` through calls keeps, and where: two counters, the arguments, and the
    // generator's frame -- last, since how large it is is known only once its body is split, and where it
    // is needs only how it is aligned.
    std::tuple<const ir::TupleType*, std::vector<std::int64_t>> description_of(Generator* found);  // check.py:19969
    // Room for a walk's description -- in this call's room, or the arena `in` named -- with the counters
    // at nought and the arguments in place.
    std::tuple<ir::Value*, std::vector<std::int64_t>> description_made(ir::Builder& builder, Iterable* walk, Span span);  // check.py:19983
    // The function that takes one turn of a generator's walk: the walk's driver.
    ir::Function* generator_next(Generator* found, const ir::TupleType* record, std::span<const std::int64_t> offsets, Span span);  // check.py:20016

    // -- a generator written into the loop that walks it -------------------------
    // The generator a loop walks, where the loop can be written with its body in place -- with the call,
    // or the description, it walks.
    std::optional<std::tuple<Generator*, Held>> written_in(const ast::ForEach* stmt);  // check.py:20193
    // Why `found` is not written into a loop, or nothing where it is.
    std::optional<std::string> not_written_in(Generator* found);  // check.py:20226
    // Lower `foreach x := g(…): BODY` with `g`'s body in place.
    ir::Value* lower_written_in(ir::Builder& builder, const ast::ForEach* stmt, ir::Function* func, const ir::Type* expected, bool produces, Generator* found, Held given);  // check.py:20253
    // The function that takes one turn of a walk handed on.
    ir::Function* next_function(Iterable* found, std::span<const ir::Type* const> kinds, Span span);  // check.py:20364
    // Walk an iterator handed in: a call per turn.
    Iteration over_an_iterator(ir::Builder& builder, ir::Value* value, const ir::IterType* ty, Span span);  // check.py:20454
    // Walk the elements of a list, which is what walking one means.
    Iteration over_a_list(ir::Builder& builder, ir::Value* value, const ir::ListType* ty, Span span);  // check.py:20487
    // Walk the characters of a string, which is what walking one means.
    Iteration over_a_string(ir::Builder& builder, ir::Value* value, Span span);  // check.py:20507
    // Walk the keys a set holds, or the pairs a dictionary holds.
    Iteration over_a_table(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span);  // check.py:20541
    // Walk a range whose step is worked out while running.
    Iteration over_a_counted_range(ir::Builder& builder, const ast::ForEach* stmt, ir::Value* first, ir::Value* last, ir::Value* step, const ir::IntType* element);  // check.py:20599
    // What a loop's expression gives out: where it starts, where it stops, how far it moves, which way it
    // runs, and of what type -- or nothing for the way, where the step is worked out while running and
    // only then says.
    std::optional<std::tuple<ir::Value*, ir::Value*, ir::Value*, std::optional<bool>, const ir::IntType*>> range_of(ir::Builder& builder, const ast::ForEach* stmt);  // check.py:20677
    // How far a range whose step is written down moves each turn, and whether it counts up.
    std::tuple<std::optional<std::int64_t>, bool> range_step(const ast::Range* written, const ir::IntType* element);  // check.py:20739
    // The names in scope that a turn of the loop may change.
    std::vector<Local*> loop_locals(const ast::Block* body, const ast::Block* alternative = nullptr);  // check.py:20764
