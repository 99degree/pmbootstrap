#########
Debugging
#########

Use ``-v`` on any action to get verbose logging:

.. code-block:: shell

  $ pmbootstrap -v build hello-world


Parse a single deviceinfo and return it as JSON:

.. code-block:: shell

  $ pmbootstrap deviceinfo_parse pine64-pinephone


Parse a single APKBUILD and return it as JSON:

.. code-block:: shell

  $ pmbootstrap apkbuild_parse hello-world


Parse a package from an APKINDEX and return it as JSON:

.. code-block:: shell

  $ pmbootstrap apkindex_parse $WORK/cache_apk_x86_64/APKINDEX.8b865e19.tar.gz hello-world


``ccache`` statistics:

.. code-block:: shell

  $ pmbootstrap stats --arch=armhf


