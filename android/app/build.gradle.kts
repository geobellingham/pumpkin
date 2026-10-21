// Pumpkin for Android: the same Pumpkin (its Python code runs through Chaquopy) with llama.cpp built for
// the phone, in a full-screen window. CI fills in src/main/python (the pumpkin package with its web UI)
// and src/main/jniLibs (llama-server for each processor type) before building; see build-android.sh.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

val pumpkinVersion = (findProperty("pumpkinVersion") as String?) ?: "0.0.0"
val parts = pumpkinVersion.split(".").map { it.toIntOrNull() ?: 0 } + listOf(0, 0, 0)

android {
    namespace = "app.pumpkin"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.pumpkin"
        minSdk = 26
        targetSdk = 35
        versionCode = parts[0] * 10000 + parts[1] * 100 + parts[2]
        versionName = pumpkinVersion
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }

    // a stable key keeps updates installable over the old app; set ANDROID_KEYSTORE_* in the repo's secrets
    val ks = System.getenv("ANDROID_KEYSTORE_FILE")
    signingConfigs {
        if (ks != null && file(ks).exists()) {
            create("pumpkin") {
                storeFile = file(ks)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS") ?: "pumpkin"
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD") ?: System.getenv("ANDROID_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("pumpkin") ?: signingConfigs.getByName("debug")
        }
    }

    // llama-server ships as lib/<abi>/libllama_server.so. Android only lets apps run programs from the folder it
    // unpacks these into, so they must be unpacked, not read from inside the APK.
    packaging { jniLibs { useLegacyPackaging = true } }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        pip { install("certifi") }
        // Pumpkin reads its web UI and assets as files, so its package is unpacked on the phone
        extractPackages("pumpkin")
        pyc { src = false }
    }
}
