rootProject.name = "TradedAssist"
enableFeaturePreview("TYPESAFE_PROJECT_ACCESSORS")

pluginManagement {
    repositories {
        google {
            mavenContent {
                includeGroupAndSubgroups("androidx")
                includeGroupAndSubgroups("com.android")
                includeGroupAndSubgroups("com.google")
            }
        }
        mavenCentral()
        gradlePluginPortal()
        maven{ url = uri("https://maven.aliyun.com/nexus/content/groups/public")}
        maven{ url = uri ("https://maven.aliyun.com/repository/google")}
        maven{ url = uri( "https://maven.aliyun.com/repository/gradle-plugin")}
        maven{ url = uri( "https://maven.aliyun.com/repository/public")}
        maven{ url = uri( "https://maven.aliyun.com/repository/jcenter")}
        maven { url = uri( "https://repo1.maven.org/maven2/") }
        maven { url = uri( "https://dl.bintray.com/kotlin/kotlin-eap") }
        maven { url = uri( "https://plugins.gradle.org/m2/")}
        maven { url = uri("https://jitpack.io") }
    }
}

dependencyResolutionManagement {
    repositories {
        google {
            mavenContent {
                includeGroupAndSubgroups("androidx")
                includeGroupAndSubgroups("com.android")
                includeGroupAndSubgroups("com.google")
            }
        }
        mavenCentral()
        maven{ url = uri("https://maven.aliyun.com/nexus/content/groups/public")}
        maven{ url = uri ("https://maven.aliyun.com/repository/google")}
        maven{ url = uri( "https://maven.aliyun.com/repository/gradle-plugin")}
        maven{ url = uri( "https://maven.aliyun.com/repository/public")}
        maven{ url = uri( "https://maven.aliyun.com/repository/jcenter")}
        maven { url = uri( "https://repo1.maven.org/maven2/") }
        maven { url = uri( "https://dl.bintray.com/kotlin/kotlin-eap") }
        maven { url = uri( "https://plugins.gradle.org/m2/")}
        maven { url = uri("https://jitpack.io") }
    }
}

include(":composeApp")