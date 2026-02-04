import { config } from 'dotenv'
import { resolve } from 'path'
import { initializeApp, cert } from 'firebase-admin/app'
import { getAuth } from 'firebase-admin/auth'
import { getFirestore } from 'firebase-admin/firestore'

// Load environment variables from .env.local
config({ path: resolve(process.cwd(), '.env.local') })

// Initialize Firebase Admin
const app = initializeApp({
  credential: cert({
    projectId: process.env.FIREBASE_ADMIN_PROJECT_ID!,
    clientEmail: process.env.FIREBASE_ADMIN_CLIENT_EMAIL!,
    privateKey: process.env.FIREBASE_ADMIN_PRIVATE_KEY?.replace(/\\n/g, '\n')!,
  }),
})

const auth = getAuth(app)
const db = getFirestore(app)

async function fixUsers() {
  try {
    // Get all Firebase Auth users
    const listUsersResult = await auth.listUsers()

    console.log(`Found ${listUsersResult.users.length} Firebase Auth users`)

    for (const user of listUsersResult.users) {
      console.log(`\nChecking user: ${user.email} (${user.uid})`)

      // Check if Firestore document exists
      const userDoc = await db.collection('users').doc(user.uid).get()

      if (userDoc.exists) {
        console.log(`  ✅ Firestore document exists`)
        console.log(`  Data:`, userDoc.data())
      } else {
        console.log(`  ❌ Firestore document missing - creating...`)

        // Create Firestore document
        const userData = {
          uid: user.uid,
          email: user.email!,
          displayName: user.displayName || user.email!.split('@')[0],
          role: 'racer',
          totalPoints: 0,
          seasonPoints: 0,
          currentStreak: 0,
          createdAt: new Date(user.metadata.creationTime),
          updatedAt: new Date(),
        }

        await db.collection('users').doc(user.uid).set(userData)
        console.log(`  ✅ Created Firestore document`)
      }
    }

    console.log('\n✅ All users fixed!')
  } catch (error) {
    console.error('❌ Error:', error)
  } finally {
    process.exit(0)
  }
}

fixUsers()
