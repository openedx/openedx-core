.. _openedx-content-adr-0005:

5. Serving Course Team Authored Static Assets
==============================================

Status
------

Accepted in principle. Partially implemented. Substantially revised in September 2026.

Context
--------

The Open edX platform needs to serve course team authored static assets as part of the authoring and learning experiences. "Static assets" in the openedx-platform context presently refers to: image files, audio files, text document files like PDFs, older video transcript files, and even JavaScript and Python files. It does NOT typically include video files, which are treated separately because of their large file size and complex workflows (processing for multiple resolutions, using third-party dictation services, attached subtitle files, etc.)

This ADR is the synthesis of various ideas that were discussed across a handful of pull requests and issues. These links are provided for extra context, but they are not required to understand this ADR:

* `File uploads + Experimental Media Server #31 <https://github.com/openedx/openedx-learning/pull/31>`_
* `File Uploads + media_server app #33 <https://github.com/openedx/openedx-learning/pull/33>`_
* `Modeling Files and File Dependencies #70 <https://github.com/openedx/openedx-learning/issues/70>`_
* `Serving static assets (disorganized thoughts) #108 <https://github.com/openedx/openedx-learning/issues/108>`_
* `Unrestricted image upload leads to stored XSS <https://github.com/openedx/openedx-platform/security/advisories/GHSA-c6xg-fh3c-vvhh>`_

Data Storage Implementation
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The underlying data models live in the ``openedx_content`` app. The most relevant models are:

* `Media in media/models.py <https://github.com/openedx/openedx-core/blob/main/src/openedx_content/applets/media/models.py>`_
* `Component and ComponentVersion in components/models.py <https://github.com/openedx/openedx-core/blob/main/src/openedx_content/applets/components/models.py>`_

Key takeaways about how this data is stored:

Currently, all assets are associated and versioned with Components, where a Component is typically an XBlock. So you don't ask for "version 5 of /static/fig1.webp"; you ask for "the /static/fig1.webp associated with version 5 of this Component".

:ref:`openedx-content-adr-0013` proposes a special type of component that only holds assets (an "Upload"), and no XBlock, so that a course's existing files and uploads can be referenced by multiple XBlocks or used independently (e.g. a PDF download).

The actual raw asset data is stored in django-storages using its hash value as the file name. This makes it cheap to make many references to the same asset data under different names and versions, but it means that we cannot simply give direct links to the raw file data to the browser (see the next section for details).

The Difficulty with Direct Links to Raw Data Files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Since the raw data is stored as objects in an S3-like store, and the mapping of file names and versions to that raw data is stored in Django models, why not simply have a Django endpoint that redirects a request to the named asset to the hash-named raw data it corresponds to?

**It will break relative links between assets.**
  The raw data files exist in a flat space with hashes for names, meaning that any relative links between assets (e.g. a JavaScript file referencing an image) would break once a browser follows the redirect.

**Setting Metadata: Content types, filenames, and caching.**
  The assets won't generally "work" unless they're served with the correct Content-Type header. For users who want to download a file, it's quite inconvenient if the filename doesn't include the correct file extension (not to mention a friendly name instead of the hash). So we need to set the Content-Type and/or Content-Disposition: ; filename=... headers.

  Setting these values for each request has proved problematic because some (but not all) S3-compatible storage services (including S3 itself) only support setting those headers for each request if you issue a signed GET request, which then gets in the way of caching and introduces the probability of browsers caching expired links, leading to all kinds of annoying cache invalidation issues.

  Setting the filename value at upload time also doesn't work because the same data may be referenced under different filenames by different Components or even different versions of the same Component.

Application Requirements
~~~~~~~~~~~~~~~~~~~~~~~~

**Relative links between assets must be preserved.**
  Assets may reference each other in relative links, e.g. a JavaScript file that references images or other JavaScript files. That means that our solution cannot require querystring-based authorization tokens in the style of S3 signed URLs, since asset files would have no way to encode those into their relative links.

**Multiple versions of the asset should be available at the same time.**
  Our system should be able to serve at minimum the current draft and published versions of an asset. Ideally, it should be able to serve any version of an asset. This is a departure from the way Studio and the LMS currently handle files and uploads, since there is currently no versioning at all–assets exist in a flat namespace at the course level and are immediately published.

Security Requirements
~~~~~~~~~~~~~~~~~~~~~

**The ``openedx_content`` app only knows about LearningPackages and content, not Learning Contexts**
  Permissions in the Open edX platform are (now) defined by the ``openedx-authz`` authorization framework, and depend on various factors like which learning context (course/library) hosts the asset and what roles the requesting user has. However, this ``openedx_content`` app is a low-level content management system, and does not know about ``openedx-authz`` nor have any way to directly check permissions/authorization.

**Assets require fine-grained permissions.**
  The Open edX platform today depends on various rules for determining who can access each asset file. The MongoDB GridFS backed ContentStore currently supports course-level access checks that can be toggled on and off for individual assets. Uploaded assets are public by default, but can optionally be "locked", which will restrict downloads to students who are enrolled in the course. Components in courses have complex authorization rules (release dates, cohorts, A/B testing, etc.), so any assets that are attached to Components should also respect those same rules. In other words, *permissions checking must be extensible*. The ``openedx_content`` app will implement the details of how to serve an asset, but it will not have the necessary models and logic to determine whether it is allowed to.

**Uploaded assets must never execute scripts with the LMS or Studio origin.**
  A user navigating to an asset URL, or a page framing it, must not result in author-supplied script running with the session cookies of the LMS or Studio.

  Any asset that a browser will render as a *document* (HTML, SVG, XML, and to a lesser extent PDF) can carry script. If such a file is served inline from the LMS or Studio origin, and a user navigates to it or a page embeds it in an ``<iframe>``, that script runs with the origin's cookies and can call any same-origin API on the victim's behalf. `GHSA-c6xg-fh3c-vvhh <https://github.com/openedx/openedx-platform/security/advisories/GHSA-c6xg-fh3c-vvhh>`_ (September 2026, rated Critical) reported exactly this against the legacy contentstore. Studio stored the uploader's declared MIME type verbatim, and the LMS served the file inline with that type and no neutralizing headers. A course author could upload an HTML file disguised as an image, and any global-staff user who opened the link would be scripted against the Studio session, including the same-origin Django admin, allowing privilege escalation to superuser.

  Note what this requirement does *not* cover. A page that deliberately loads an uploaded file as a subresource (``<script src>``, ``<link rel=stylesheet>``) executes it with the page's own origin no matter where the file is served from. That is a content authoring concern (the same one raised by HTML XBlocks that contain script), not an asset serving concern, and no serving-side measure, including a separate domain, changes it.

  Note: the Open edX platform already allows authors to put arbitrary HTML in their course content, including scripts, so reproducing the privilege escalation exploit of that security advisory is trivial, without any need to involve static assets at all. However, there is a long-term goal of eliminating such risks from the platform entirely, so we definitely want to close down any such risks in the way static assets are served.

**The serving layer, not the uploader, decides the Content-Type.**
  The MIME type recorded at upload time is a hint. The response's ``Content-Type`` must come from an allowlist of types that are safe to serve inline (raster images, audio, video, fonts, CSS, JavaScript, JSON, plain text, PDF, and SVG and HTML only because they are sandboxed). Anything else is served as ``application/octet-stream`` with ``Content-Disposition: attachment``, and every response carries ``X-Content-Type-Options: nosniff`` so a browser cannot sniff an "image" into HTML.

Operational Requirements
~~~~~~~~~~~~~~~~~~~~~~~~

**The asset server must be capable of handling high levels of traffic.**
  Django views are poor choice for streaming files at scale, especially when deploying using WSGI (as Open edX does), since it will tie down a worker process for the entire duration of the response. While a Django-based streaming response may sufficient for small-to-medium traffic sites, we should allow for a more scalable solution that fully takes advantage of modern CDN capabilities.

**Serving assets should not *require* ASGI deployment.**
  Deploying the LMS and Studio using ASGI would likely substantially improve the scalability of a Django-based streaming solution, but migrating and testing this new deployment type for the entire stack is a large task and is considered out of scope for this project.

**Serving assets must not require operators to provision additional domains.**
  Tutor, the supported deployment method, places the LMS, Studio and the MFEs under a single base domain and manages certificates for them. A design that only becomes secure once an operator buys and configures a second registrable domain would leave most sites running the insecure configuration.

Decision
--------

URLs
~~~~

Assets are served from the LMS and Studio hosts themselves. There is no separate asset host.

The format will be: ``https://{lms_or_studio_host}/...{context}.../{component_key}/{version}/{filepath}``

A more concrete example: ``https://demo.openedx.org/assets/content_libraries/lib:Axim:200/xblock.v1:problem@multi_choice_8/v4/static/images/fig1.png``

The ``version`` can be:

* ``draft`` indicating the latest draft version (viewed by authors in Studio).
* ``published`` indicating the latest published version (viewed by students in the LMS)
* ``v{num}`` meaning a specific version–e.g. ``v20`` for version 20.

Response Headers
~~~~~~~~~~~~~~~~

Every asset response, whether it succeeds or fails, carries:

* ``Content-Security-Policy: sandbox``
* ``X-Content-Type-Options: nosniff``

``sandbox`` is a *document* directive. It has no effect on subresource loads, so ``<img>``, ``<script src>``, ``<link>``, ``<audio>``, ``<video>``, fonts and ``fetch()`` continue to work exactly as before. It takes effect only when the asset is loaded as a document: a top-level navigation to the asset URL, or an ``<iframe>``, ``<object>`` or ``<embed>``. In those cases the document gets an opaque origin and scripting is disabled, so an uploaded HTML or SVG file renders but cannot read cookies, cannot set cookies on the parent domain, and cannot make credentialed same-origin requests. Django's CSRF check additionally rejects the ``null`` ``Origin`` such a document would send.

This is the same policy the upstream contentserver now applies, so a course whose ``/static/`` files migrate from contentstore to ``openedx_content`` (per :ref:`openedx-content-adr-0013`) sees no change in behaviour.

``Content-Type`` is chosen by the serving code from the allowlist described in the security requirements, using the stored media type and file extension as inputs. Types outside the allowlist are served as ``application/octet-stream`` with ``Content-Disposition: attachment; filename="..."``, using the asset's path within the component for the filename.

**"Insecure Legacy Assets"**: The relaxed form ``sandbox allow-scripts allow-forms allow-popups`` would keep the opaque origin while letting scripts run, which would preserve the legacy pattern of iframing an uploaded HTML5 interactive from an HTML block. It is not adopted here. Its safety depends on the site's cookies being ``SameSite=Lax`` or stricter, but `the platform defaults to SameSite=None <https://github.com/openedx/openedx-platform/blob/aa5ec07c61a90ca4ef0ae05eebddc653e5d4e033/lms/envs/production.py#L140>`_. Any per-asset relaxation is left to a future decision, and would be a platform-level flag in the spirit of ``course_assets.allow_unsafe_asset_rendering``, passed into the ``openedx_content`` serving API rather than decided by it.

Reverse Proxy Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Django is poorly suited to serving large static assets, particularly when deployed using WSGI. Instead of streaming the actual file data, the Django views serving assets will make use of the ``X-Accel-Redirect`` header. This header is supported by both Caddy and Nginx, and will cause them to fetch the data from the specified URI to send to the user. This redirect happens internally in the proxy and does *not* change the browser address. For sites using an object store like S3, the Django view will generate and send a signed URL to the asset. For sites using file-based Django media storage, the view will send a URL that Caddy or Nginx knows how to load from the file system.

The proxy that fronts the LMS and Studio (Caddy in Tutor) handles the internal redirect for the ``/assets/`` path prefix. It is a hard requirement that the proxy configuration for that internal location **forces** ``Content-Security-Policy: sandbox`` and ``X-Content-Type-Options: nosniff`` itself, and does not rely on them being copied from the Django response. Proxies generally carry only a fixed set of headers (such as ``Content-Type``, ``Content-Disposition`` and ``Cache-Control``) from the Django response across an ``X-Accel-Redirect`` and take the rest from the redirect target, so a security header set only by the view can silently disappear. The reference Tutor configuration and any deployment documentation must state this, and the test suite must include a request that goes through the proxy, not only through the Django test client.

Until a deployment has the proxy configured, a Django-streamed fallback (as the interim Studio endpoints do today) is acceptable for low-traffic use, provided it sets the same headers in the view. This same mechanism can be used to serve "Insecure Legacy Assets", if required on a per-asset basis.

Django View Implementations
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``openedx_content`` provides an API that, given a component version and asset path, returns an ``HttpResponse`` with the ``X-Accel-Redirect`` header and all of the required metadata headers (content type, sandbox, nosniff, disposition, ETag, caching, and the ``X-Open-edX-*`` informational headers). It never enforces permissions nor returns ``401`` or ``403``; it does not know who is asking (see next decision).

Permissions
~~~~~~~~~~~

The ``openedx_content`` API contains the logic for looking up and serving assets, but it is the responsibility of an app in Studio or the LMS to wrap it with permissions checking logic using ``openedx-authz``. This logic may vary from app to app. For instance, Studio would likely implement a simple permissions checking model that only examines the learning context and restricts access to course staff. LMS might eventually use a much more sophisticated model that looks at the individual Component that an asset belongs to, and which prohibits access to any version other than the current published version.

Authentication
~~~~~~~~~~~~~~

Because assets are served from the same origin as the LMS and Studio REST API, requests carry the ordinary session cookie and no additional login flow is needed. Pages do not need to do anything special before loading assets.

Assets that are publicly readable will not require authentication.

Asset requests may return a 403 error if the user is logged in but not authorized to download the asset. They will return a 401 error for users that are not authenticated.

Masquerading works the same way it does for any other LMS view, since it is the same session. Whether a masqueraded request is evaluated against the masqueraded user's permissions is a decision for the platform app's permission check, not for ``openedx_content``.

Rejected Alternatives
---------------------

Checking Permissions Within ``openedx_content`` Itself
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

At the time of this ADR, ``openedx_content`` does not (yet) have a table that maps each :class:`LearningPackage` to its corresponding learning context, and ``openedx_content`` is thus unaware of the user-visible identifier (``course-v1:...`` for courses, ``lib:...`` for libraries, etc.) for each Learning Package. Since permissions are currently defined within the ``openedx-authz`` system based on these identifier strings, and since no part of ``openedx-core`` yet interacts with the ``authz`` system, we have proposed the design described above.

On the other hand, if were to add a dependency on ``openedx-authz`` to ``openedx-core`` and to either:

* change authoring permissions so that ``authz`` rules define them on a per-LearningPackage basis, not on a per-context basis; or
* add a table to ``openedx_content`` that maps each :class:`LearningPackage` to at most one learning context identifier (``course-v1:...`` for courses, ``lib:...`` for libraries, etc.),

then we could have a single REST API for serving assets implemented by the ``openedx_content`` app itself, rather than providing a low-level API that gets combined with authorization code and integrated into various higher-level REST APIs.

In the first option (permissions are based on LearningPackage), it would also be necessary to associate each :class:`LearningPackage` with an organization, to support org-based permissions.

A disadvantage of this approach is that even though ``openedx_content`` would have basic context about each static asset file, it wouldn't have enough information to allow ``authz`` to enforce fine-grained access control, such as anything based on cohort, release dates, A/B testing, or other high-level features, as only the LMS has enough context for that.

Serving Assets from a Separate Domain
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The original version of this ADR required that assets be served from an entirely different registrable domain from the LMS and Studio (for example ``lms.assets.sandbox.openedx-assets.io`` for an LMS at ``learning.openedx.org``), so that script in an uploaded file could never run with the LMS or Studio origin. A distinct subdomain was explicitly ruled out because Open edX scopes its session and JWT cookies to the parent domain, so every subdomain receives them.

The threat is real, as `this advisory <https://github.com/openedx/openedx-platform/security/advisories/GHSA-c6xg-fh3c-vvhh>`_ demonstrates, but the separate domain is not the right remedy for this platform:

* It required every operator to purchase and manage a second domain and certificate, which Tutor does not support out of the box. Sites that did not do so would have had no protection at all.
* Because cookies do not cross domains, the design needed a token-based cross-domain authorization flow: a check-login ``<script>`` in the ``<head>`` of every page that shows non-public assets, a cache-backed one-time token, a second session on the asset domain, and unsolved masquerading. All of that machinery, and the per-asset login redirection variant that was rejected alongside it, disappears once assets are same-origin.
* Cross-origin assets also required CORS for fonts, ES modules, canvas reads and ``fetch()``.
* It was not sufficient on its own. The asset domain had its own session, so an unsandboxed uploaded HTML file navigated to on that domain could still read and exfiltrate every other draft or locked asset the victim could access there. The ``sandbox`` CSP header was needed anyway, and once present its neutralizes the same-origin attack too.

The one residual benefit of an isolated domain is that a per-course "allow unsafe rendering" escape hatch would expose only the asset domain's session rather than the whole platform. That was judged not worth the operational cost. Operators who want that isolation can still front the ``/assets/`` path with a different hostname at the proxy layer, but this ADR does not design for it and the authentication flow that a *different site* would need is not provided.

Serving Everything as an Attachment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Sending ``Content-Disposition: attachment`` on every asset would also prevent uploaded HTML and SVG from rendering, because browsers apply it to navigation and frames but ignore it for subresource loads. It was rejected as the primary defense because course teams legitimately link learners to PDFs and images to view in the browser, and frame uploaded HTML from HTML blocks. ``sandbox`` neutralizes the content while still letting it render. ``attachment`` is retained only for types outside the inline allowlist.

Validating Content at Upload Time as the Only Defense
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Rejecting HTML and SVG uploads, or sniffing file contents at upload, was rejected as the sole measure. Polyglot files defeat sniffing, large amounts of already-uploaded content would remain unprotected, and restrictions at the serving layer are more comprehensive. Stricter upload validation in the platform is welcome as defense in depth, and is still worth implementing, either as an API within ``openedx_content`` or in other layers of the platform, but is left out of scope for this ADR.

Changelog
---------

2026-09-18:

* Major revisions to every section. Added `sandbox` header and removed plan for serving from separate domain. Clarified many security requirements. Clarified separation of concerns around authentication. Planned for combining LMS & Studio domains together in the future.

2023-12-04:

* Initial version
  