---
name: sdlc-angularjs-ui
description: Implement AngularJS 1.x user interfaces, forms, components and UI tests within an approved application change. Does not apply to Angular 2+.
---

# AngularJS UI implementation

Inspect package manifests, the installed AngularJS version, module registration,
routing, templates and existing tests before selecting APIs. Match the existing
JavaScript or TypeScript setup. Do not introduce modern Angular decorators,
standalone components or a framework migration unless the approved task asks for it.

Follow the application's controller/component conventions. Use components and
explicit bindings where supported and consistent with the repository; do not
convert unrelated controllers. Keep API access in existing service boundaries.
Use minification-safe dependency injection consistent with the build pipeline.

For asynchronous UI, model loading, empty, success and failure states. Keep
updates within AngularJS's digest mechanism using existing $http/$q patterns;
callbacks from outside AngularJS may need $evalAsync. Dispose subscriptions,
listeners and timers when their owning scope/component is destroyed. Avoid
expensive work in template expressions and unstable identities in repeated lists.

Derive payloads, nullability and validation from the agreed API contract. Client
validation supplements server validation. Render untrusted text as text; avoid
trusting arbitrary HTML or bypassing sanitization. Provide labels, keyboard
operation and accessible feedback for pending and failed actions.

Add tests using the repository's existing AngularJS test harness, including
binding changes, request failures and form behaviour relevant to the task. Mock
HTTP at the established boundary and verify pending requests are cleaned up.
Do not introduce a new test framework or weaken assertions to obtain a pass.

In SDLC delivery, write implementation and tests only within the assigned scope.
Python runs verification; report required commands and missing dependencies,
never claim browser or test success without evidence. The example registry's
UI suite expects `npm --prefix app/ui run verify`; maintainers must map that suite
to the actual application's build, lint and noninteractive test commands.
