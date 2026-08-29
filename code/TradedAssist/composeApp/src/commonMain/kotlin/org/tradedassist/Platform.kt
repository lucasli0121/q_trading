package org.tradedassist

interface Platform {
    val name: String
}

expect fun getPlatform(): Platform